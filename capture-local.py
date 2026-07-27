#!/usr/bin/env python3
"""Local browser capture — a no-API-key alternative to urlscan.io.

Loads the target in headless Chromium (Playwright) and records the DOM, the full
network request map, response statuses, cookies, and external domains contacted.
This reproduces most of what urlscan returns. The one thing it cannot give is
urlscan's independent, non-proxied vantage point: if this host sits behind a
TLS-intercepting egress proxy, the page still loads through that proxy (content
is fine; the TLS leg is not — assess TLS via Qualys SSL Labs / urlscan.io instead).

Usage: capture-local.py <url> <out_dir>
Writes: <out>/4-local-capture.json, <out>/dom.html
"""

import json
import os
import sys
from collections import Counter
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: capture-local.py <url> <out_dir>")
        return 2
    url, out = sys.argv[1], sys.argv[2].rstrip("/")

    # Browser stages must reach the site directly: Chromium mis-handles the TLS-intercepting
    # egress proxy and RSTs HTTPS. The audit host has open outbound (see run-audit.sh header),
    # and --ignore-certificate-errors covers the transparent interception, so clear the proxy vars.
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ.pop(_v, None)

    requests: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--ignore-certificate-errors"])
        ctx = browser.new_context(ignore_https_errors=True)
        page = ctx.new_page()

        def on_response(resp):
            try:
                requests.append(
                    {
                        "url": resp.url,
                        "method": resp.request.method,
                        "type": resp.request.resource_type,
                        "status": resp.status,
                        "domain": urlparse(resp.url).netloc,
                    }
                )
            except Exception:
                pass

        page.on("response", on_response)
        resp = page.goto(url, wait_until="networkidle", timeout=60000)
        try:
            page.wait_for_timeout(2000)
        except Exception:
            pass

        dom = page.content()
        cookies = ctx.cookies()
        title = page.title()
        final_url = page.url
        status = resp.status if resp else None
        browser.close()

    domains = sorted({r["domain"] for r in requests if r["domain"]})
    summary = {
        "input_url": url,
        "final_url": final_url,
        "title": title,
        "status": status,
        "request_count": len(requests),
        "resource_types": dict(Counter(r["type"] for r in requests)),
        "domains_contacted": domains,
        "cookies": [{"name": c.get("name"), "domain": c.get("domain")} for c in cookies],
        "requests": requests,
    }

    with open(f"{out}/4-local-capture.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    with open(f"{out}/dom.html", "w") as fh:
        fh.write(dom)

    print(f"local capture: {final_url} [{status}] '{title}'")
    print(f"  requests: {len(requests)}  domains: {len(domains)}  cookies: {len(cookies)}")
    print(f"  domains contacted: {', '.join(domains) or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
