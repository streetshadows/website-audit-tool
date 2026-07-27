#!/usr/bin/env python3
"""Performance + Lighthouse audit — stage 10.

Standard method: LOCAL Lighthouse. PSI (PageSpeed Insights) is NOT required and is
not used by default — it added little value for the cost of a key, so it is only
used if PSI_KEY is explicitly set (power-user opt-in for CrUX field data).

IMPORTANT honesty note: local Lighthouse runs through this environment's egress
proxy from a datacenter, so timing metrics (LCP/TBT/Speed Index) and the overall
Performance SCORE are NOT representative of real users — they are marked
"indicative only". The environment-INDEPENDENT signals are trustworthy and are
what we report as findings: structural performance opportunities (image sizing,
explicit dimensions, render-blocking, compression, modern formats) plus the
Accessibility / Best-Practices / SEO categories. CLS (layout stability) is largely
structural and reported with lighter caveat.

Cross-verification: image opportunities are checked against stage-6 qa-check
findings; the a11y category against stage-9 axe; SEO basics against stage-7 SEO.

Usage: perf-check.py <out_dir>   (reads <out>/crawl/urls.txt)
Env: PERF_MAX_PAGES(1) PERF_FORM(mobile) PSI_KEY(optional) CHROME_PATH
"""

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import standards  # standards & best-practice knowledge base (sibling module)


def psi(url, strategy, key):
    api = (
        "https://www.googleapis.com/pagespeedonline/v5/runPagespeed?"
        f"url={urllib.parse.quote(url, safe='')}&strategy={strategy}"
        "&category=performance&category=accessibility&category=seo&category=best-practices"
    )
    if key:
        api += f"&key={key}"
    try:
        with urllib.request.urlopen(api, timeout=60) as r:
            return json.load(r), None
    except Exception as e:
        code = getattr(e, "code", "")
        return None, f"{type(e).__name__} {code}"


def _default_chrome():
    """Resolve the installed Chromium from Playwright rather than hardcoding a version-pinned
    path (which drifts every time Playwright bumps its bundled Chromium build)."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return p.chromium.executable_path
    except Exception:
        return ""


def run_local_lh(url, form):
    chrome = os.environ.get("CHROME_PATH") or _default_chrome()
    cmd = [
        "lighthouse",
        url,
        "--quiet",
        "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage --ignore-certificate-errors",
        "--output=json",
        "--output-path=stdout",
        "--only-categories=performance,accessibility,best-practices,seo",
        f"--form-factor={form}",
    ]
    if form == "mobile":
        cmd.append("--screenEmulation.mobile")
    else:
        cmd.append("--preset=desktop")
    env = dict(os.environ, CHROME_PATH=chrome)
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        return json.loads(out.stdout), None
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"


def summarize_lh(lh, source):
    cats = {k: (round(v["score"] * 100) if v.get("score") is not None else None) for k, v in lh["categories"].items()}
    A = lh["audits"]

    def dv(aid):
        return A.get(aid, {}).get("displayValue", "")

    metrics = {
        m: dv(m)
        for m in (
            "first-contentful-paint",
            "largest-contentful-paint",
            "cumulative-layout-shift",
            "total-blocking-time",
            "speed-index",
        )
    }
    # environment-independent opportunities/diagnostics (failed = score < 0.9)
    structural_ids = [
        "unsized-images",
        "uses-responsive-images",
        "uses-optimized-images",
        "modern-image-formats",
        "efficient-animated-content",
        "render-blocking-resources",
        "unminified-css",
        "unminified-javascript",
        "unused-css-rules",
        "uses-text-compression",
        "uses-long-cache-ttl",
        "uses-rel-preconnect",
        "font-display",
        "third-party-summary",
    ]
    opps = []
    for aid in structural_ids:
        a = A.get(aid)
        if a and a.get("score") is not None and a["score"] < 0.9:
            opps.append({"id": aid, "title": a.get("title"), "detail": a.get("displayValue", "")})

    # failed a11y / best-practices / seo audits
    def failed(cat):
        out = []
        for ref in lh["categories"].get(cat, {}).get("auditRefs", []):
            a = A.get(ref["id"], {})
            if a.get("score") is not None and a["score"] < 1 and a.get("scoreDisplayMode") in ("binary", "numeric"):
                out.append({"id": ref["id"], "title": a.get("title")})
        return out

    return {
        "source": source,
        "scores": cats,
        "metrics_indicative": metrics,
        "structural_opportunities": opps,
        "failed_accessibility": failed("accessibility"),
        "failed_best_practices": failed("best-practices"),
        "failed_seo": failed("seo"),
    }


def crux(d):
    """Real-user (CrUX) field data from PSI, if the origin has enough traffic."""
    out = {}
    for scope, key in (("page", "loadingExperience"), ("origin", "originLoadingExperience")):
        le = d.get(key, {})
        m = le.get("metrics")
        if m:
            out[scope] = {k: {"p75": v.get("percentile"), "rating": v.get("category")} for k, v in m.items()}
            out[scope]["overall"] = le.get("overall_category")
    return out or None


def main():
    if len(sys.argv) < 2:
        print("usage: perf-check.py <out_dir>")
        return 2
    out = sys.argv[1].rstrip("/")
    # Lighthouse launches Chrome, which mis-handles the TLS-intercepting egress proxy and RSTs
    # HTTPS. The audit host has open outbound (see run-audit.sh header), so clear the proxy vars.
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ.pop(_v, None)
    urls = [u.strip() for u in open(os.path.join(out, "crawl", "urls.txt")) if u.strip()]
    cap = int(os.environ.get("PERF_MAX_PAGES", 1))
    urls = urls[:cap]
    key = os.environ.get("PSI_KEY")
    # With a key we run BOTH form factors and pull real-user field data; without, one local pass.
    strategies = (
        os.environ.get("PERF_STRATEGIES", "mobile,desktop").split(",")
        if key
        else [os.environ.get("PERF_FORM", "mobile")]
    )
    report = {"psi_key_present": bool(key), "strategies": strategies, "pages": {}}
    for u in urls:
        report["pages"][u] = {}
        for form in strategies:
            d, err = psi(u, form, key)
            if d:
                s = summarize_lh(d["lighthouseResult"], "PageSpeed Insights (Google servers — representative)")
                cf = crux(d)
                if cf:
                    s["crux_field_data"] = cf
                report["pages"][u][form] = s
            else:
                lh, lerr = run_local_lh(u, form)
                if lh:
                    s = summarize_lh(
                        lh,
                        "LOCAL Lighthouse — timing/score INDICATIVE ONLY "
                        "(datacenter+proxy); structural audits trustworthy",
                    )
                    s["psi_error"] = err
                    report["pages"][u][form] = s
                else:
                    report["pages"][u][form] = {"error": f"PSI: {err}; local: {lerr}"}

    # ---- standards & best-practice citations: Core Web Vitals headline metrics
    # (always, as the reference targets) + each structural opportunity that fired.
    # Filter to known perf entries so Lighthouse audit ids don't hit the axe fallback. ----
    fired = ["cwv-lcp", "cwv-cls", "cwv-inp"]
    ran = False
    for byform in report["pages"].values():
        for s in byform.values():
            if s.get("error"):
                continue
            ran = True
            for o in s.get("structural_opportunities", []):
                if o["id"] in standards.STANDARDS:
                    fired.append(o["id"])
    report["standards"] = standards.applied(fired) if ran else {}
    json.dump(report, open(os.path.join(out, "9-perf.json"), "w"), indent=2)

    L = ["== PERFORMANCE / LIGHTHOUSE =="]
    L.append(
        "method: "
        + (
            "PageSpeed Insights (PSI_KEY set)"
            if report["psi_key_present"]
            else "local Lighthouse (standard method; scores + structural audits reliable, timing indicative)"
        )
    )
    for u, byform in report["pages"].items():
        L.append(f"[{u}]")
        for form, s in byform.items():
            if s.get("error"):
                L.append(f"  ({form}) ERROR: {s['error']}")
                continue
            rep = "PageSpeed" in s["source"]
            L.append(f"  ({form}) source: {s['source']}")
            L.append(f"    scores: {s['scores']}")
            L.append(f"    metrics{'' if rep else ' (INDICATIVE ONLY — not real-user)'}: {s['metrics_indicative']}")
            if s.get("crux_field_data"):
                for scope, m in s["crux_field_data"].items():
                    L.append(f"    CrUX {scope} real-user: { {k: v for k, v in m.items()} }")
            if s["structural_opportunities"]:
                L.append("    perf opportunities (structural — trustworthy):")
                for o in s["structural_opportunities"]:
                    L.append(f"        - {o['title']} {('(' + o['detail'] + ')') if o['detail'] else ''}")
            if s["failed_accessibility"]:
                L.append(
                    "    a11y audits failed (cross-check stage 9 axe): "
                    + ", ".join(a["id"] for a in s["failed_accessibility"])
                )
            if s["failed_best_practices"]:
                L.append("    best-practices failed: " + ", ".join(a["id"] for a in s["failed_best_practices"]))
            if s["failed_seo"]:
                L.append(
                    "    seo failed (basics; deeper gaps in stage 7): " + ", ".join(a["id"] for a in s["failed_seo"])
                )
    L.append("\nNOTE: local Lighthouse is the standard performance method for this toolkit.")
    L.append("Timing (LCP/FCP/SI) is indicative only from a datacenter; category scores and structural")
    L.append("audits (CLS, unsized images, render-blocking, etc.) + a11y/BP/SEO are reliable. PSI is not required.")
    if report.get("standards"):
        L.append("\n-- WHY / STANDARDS (Core Web Vitals targets + each opportunity, with references) --")
        for code in report["standards"]:
            L.append("  - " + standards.format_text(code, "fail"))
    txt = "\n".join(L)
    open(os.path.join(out, "9-perf.txt"), "w").write(txt + "\n")
    print(txt[:2800])
    # Non-zero when no page produced Lighthouse scores (e.g. Chrome not found): a non-empty
    # 9-perf.json containing only an error object must not be recorded as a passing stage.
    return 0 if ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
