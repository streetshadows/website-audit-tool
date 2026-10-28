#!/usr/bin/env python3
"""Anonymous over-fetch check — stage 4b.

A passive, read-only record of the JSON a *logged-out* visitor's own browser already
receives when it loads public pages. It compares the fields a backend returns with the
fields the page actually renders, so a field that is delivered but never displayed can be
surfaced for the owner to review (OWASP API3:2023, excessive data exposure). It performs
no authentication, sends no crafted requests, and writes no field values — only the shape
of each response (status, size, field names, non-null counts, rendered yes/no).

Guardrails, enforced in code:
  * values are examined in memory only and are never written or printed;
  * stored endpoint keys keep query-parameter NAMES, not their values;
  * each page loads in a fresh, cookie-less context (no credentials, ever);
  * a protected-route result only means something when the public "control" pages did
    produce data requests — otherwise it is reported as inconclusive, not as a pass.

Usage: overfetch-check.py <out_dir>   (reads <out>/crawl/urls.txt)
Env: OVERFETCH_MAX_PAGES(10) PROTECTED_URLS PROTECTED_WATCH_MS(5000)
Writes: <out>/4b-overfetch.json, <out>/4b-overfetch.txt
"""

import json
import os
import sys
from urllib.parse import parse_qsl, urlparse

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netutil  # shared per-host pacing (sibling module)
import standards  # citations KB (sibling module)


def endpoint_key(method, url):
    """origin + path + sorted query-parameter NAMES (values dropped — filters can hold data)."""
    p = urlparse(url)
    names = sorted({k for k, _ in parse_qsl(p.query, keep_blank_values=True)})
    return f"{method} {p.scheme}://{p.netloc}{p.path}" + (f"?{'&'.join(names)}" if names else "")


def rows_of(data):
    """Record-like objects in a JSON payload: a list of objects, a list under a common
    wrapper key, or a single object."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for k in ("data", "items", "results", "rows", "records", "nodes"):
            if isinstance(data.get(k), list):
                return [r for r in data[k] if isinstance(r, dict)]
        return [data]
    return []


def field_shape(rows, page_text):
    """Per field: non_null count, JSON type, and rendered (True/False/None).
    Values are only tested for presence in the already-public page text; none are kept."""
    text = page_text.lower()
    fields = {}
    for r in rows:
        for k, v in r.items():
            f = fields.setdefault(k, {"non_null": 0, "type": None, "_checkable": 0, "_rendered": 0})
            if v is None:
                continue
            f["non_null"] += 1
            f["type"] = type(v).__name__
            if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                s = str(v).strip().lower()
                if len(s) >= 4:  # ignore trivially short values that match by chance
                    f["_checkable"] += 1
                    if s in text:
                        f["_rendered"] += 1
    out = {}
    for k, f in fields.items():
        if f["_checkable"] == 0:
            rendered = None  # no value long enough to look for
        else:
            rendered = f["_rendered"] > 0
        out[k] = {"non_null": f["non_null"], "type": f["type"], "rendered": rendered}
    return out


def capture(browser, url, watch_ms):
    """Load one page in a fresh anonymous context; return its data responses (shape only)."""
    payloads = []

    def on_response(resp):
        ct = (resp.headers.get("content-type") or "").lower()
        if "json" not in ct:
            return
        rt = resp.request.resource_type
        if rt not in ("xhr", "fetch"):
            return
        try:
            body = resp.json()
        except Exception:
            return
        payloads.append((resp.request.method, resp.url, resp.status, resp.headers.get("content-length"), body))

    ctx = browser.new_context(ignore_https_errors=True, user_agent=netutil.AUDIT_UA)
    pg = ctx.new_page()
    pg.on("response", on_response)
    result = {"url": url, "final_url": url, "redirected": False, "endpoints": {}, "error": None}
    try:
        netutil.PACER.wait(url)
        pg.goto(url, wait_until="networkidle", timeout=45000)
        pg.wait_for_timeout(watch_ms)
        page_text = pg.evaluate("() => document.body ? document.body.innerText : ''")
        result["final_url"] = pg.url
        result["redirected"] = urlparse(pg.url).path.rstrip("/") != urlparse(url).path.rstrip("/")
    except Exception as e:
        page_text = ""
        result["error"] = f"{type(e).__name__}: {str(e)[:80]}"
    for method, u, status, clen, body in payloads:
        rows = rows_of(body)
        if not rows:
            continue
        key = endpoint_key(method, u)
        fields = field_shape(rows, page_text)
        not_rendered = sorted(k for k, f in fields.items() if f["rendered"] is False)
        result["endpoints"][key] = {
            "status": status,
            "content_length": clen,
            "rows": len(rows),
            "field_count": len(fields),
            "fields": fields,
            "fields_not_rendered": not_rendered,
        }
    ctx.close()
    return result


def main():
    if len(sys.argv) < 2:
        print("usage: overfetch-check.py <out_dir>")
        return 2
    out = sys.argv[1].rstrip("/")
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ.pop(_v, None)
    try:
        urls = [u.strip() for u in open(os.path.join(out, "crawl", "urls.txt")) if u.strip()]
    except Exception:
        urls = []
    cap = int(os.environ.get("OVERFETCH_MAX_PAGES", 10))
    public_urls = urls[:cap]
    protected = [u.strip() for u in os.environ.get("PROTECTED_URLS", "").replace(",", " ").split() if u.strip()]
    watch_ms = int(os.environ.get("PROTECTED_WATCH_MS", 5000))

    public, prot = [], []
    with sync_playwright() as p:
        b = p.chromium.launch(
            args=["--ignore-certificate-errors"], executable_path=os.environ.get("PW_CHROMIUM_EXECUTABLE") or None
        )
        for u in public_urls:
            public.append(capture(b, u, 2000))
        for u in protected:
            prot.append(capture(b, u, watch_ms))
        b.close()

    # The public pages are the control: a protected route making "no data request" is only
    # meaningful if the public pages did make some (i.e. the capture mechanism actually works).
    public_data_reqs = sum(len(r["endpoints"]) for r in public)
    control_ok = public_data_reqs > 0

    over = []
    for r in public:
        for key, e in r["endpoints"].items():
            if e["fields_not_rendered"]:
                over.append({"page": r["url"], "endpoint": key, "fields_not_rendered": e["fields_not_rendered"]})

    prot_summary = []
    for r in prot:
        data_reqs = len(r["endpoints"])
        prot_summary.append(
            {
                "url": r["url"],
                "redirected": r["redirected"],
                "final_url": r["final_url"],
                "data_requests": data_reqs,
                "inconclusive": (data_reqs == 0 and not control_ok),
                "error": r["error"],
            }
        )

    report = {
        "method": "anonymous (logged-out) browser capture; response SHAPE only, no values stored",
        "public_pages_checked": len(public),
        "public_data_requests": public_data_reqs,
        "control_ok": control_ok,
        "over_fetch": over,
        "public": public,
        "protected": prot_summary,
        "standards": standards.applied(["api-excessive-data"]) if over else {},
    }
    json.dump(report, open(os.path.join(out, "4b-overfetch.json"), "w"), indent=2)

    L = ["== ANONYMOUS OVER-FETCH (logged-out; response shape only, no values stored) =="]
    L.append(
        f"public pages: {len(public)}; data requests seen: {public_data_requests_str(public_data_reqs, control_ok)}"
    )
    if not control_ok:
        L.append("  [!] control: no data requests on public pages — a 'zero' on protected routes is INCONCLUSIVE")
    if over:
        L.append(f"\nFields delivered to anonymous visitors but NOT rendered (verify necessity): {len(over)}")
        for o in over[:20]:
            L.append(f"    {o['endpoint']}")
            L.append(f"        {o['page']}")
            L.append(f"        not rendered: {', '.join(o['fields_not_rendered'][:20])}")
    else:
        L.append("\nNo delivered-but-unrendered fields detected on the public pages checked.")
    if prot_summary:
        L.append("\nProtected routes (logged out):")
        for s in prot_summary:
            tag = "redirected" if s["redirected"] else "NO redirect"
            dq = f"{s['data_requests']} data request(s)"
            if s["inconclusive"]:
                dq += " — INCONCLUSIVE (control produced none)"
            L.append(f"    {s['url']} -> {tag}; {dq}" + (f"; error {s['error']}" if s["error"] else ""))
    if over and report["standards"]:
        L.append("\n-- WHY / STANDARDS --")
        for code in report["standards"]:
            L.append("  - " + standards.format_text(code, "fail"))
    txt = "\n".join(L)
    open(os.path.join(out, "4b-overfetch.txt"), "w").write(txt + "\n")
    print(txt[:2600])
    return 0


def public_data_requests_str(n, control_ok):
    return f"{n}" + ("" if control_ok else " (control weak)")


if __name__ == "__main__":
    raise SystemExit(main())
