# Website Audit — repeatable, scalable process

A single command audits any website end-to-end. Designed to be **reliable over
fast**: sequential, bounded, resumable per-stage, and works on a 1-page site or a
large multi-page site (bounded by caps).

```bash
./run-audit.sh https://example.com
# tuning (all optional):
MAX_PAGES=500 MAX_DEPTH=5 DELAY=0.3 \
SEO_MAX_PAGES=500 QA_MAX_PAGES=40 QA_VIEWPORTS="390,768,1440" \
URLSCAN_API_KEY=... PERF_STRATEGIES="mobile,desktop" \
  ./run-audit.sh https://example.com
```

## Stages (artifacts written to ./audit/)

| # | Stage | Script | Scope | Output |
|---|-------|--------|-------|--------|
| 0 | Discovery / crawl | `crawl.py` | site | `crawl/urls.txt`, `crawl/discovery.json`, `crawl/robots.txt` |
| 1 | DNS / infra + RDAP-over-HTTPS registration (port-43-free) | `dig`,`curl` (RDAP),`httpx` | host | `1-dns.txt`, `1-rdap-*.json`, `1-whois.txt`, `1-httpx.json` |
| 2 | TLS (proxy-safe) | SSL Labs, urlscan | host | `2-tls-*.{json,txt}` |
| 3 | Headers / misconfig / tech | `curl`,`nuclei`,`whatweb` | host | `3-headers.txt`, `3-nuclei.txt`, `3-whatweb.txt` |
| 4 | Reputation / external render | urlscan.io | host | `4-urlscan*.{json,txt,png}` |
| 5 | Responsive screenshots | `shot-scraper` | homepage | `shot-*.png` |
| 6 | Deep UI / image / spelling QA | `qa-check.py` | homepage (template/asset deep dive) | `6-qa.{json,txt}`, `qa-issue-*.png` |
| 6b| Deep QA on extra pages | `qa-check.py` (loop) | up to `QA_DEEP_PAGES` | `deep/<slug>/6-qa.*` |
| 7 | SEO / metadata / links | `seo-check.py` | **all pages** | `5-seo.{json,txt}` |
| 8 | Multi-page responsive regression | `qa-site.py` | **all pages (capped)** | `7-qa-site.{json,txt}`, `qa-pages/*.png` |
| 9 | Accessibility (WCAG) | `a11y-check.py` | pages (capped) | `8-a11y.{json,txt}` |
| 10| Performance / Lighthouse | `perf-check.py` | pages (capped) | `9-perf.{json,txt}` |

## What each stage checks

**0 — Discovery (`crawl.py`).** robots.txt (presence, `Sitemap:` directives, and
**AI-crawler policy** for GPTBot/ClaudeBot/Google-Extended/CCBot/PerplexityBot/…),
recursive sitemap parsing (sitemap index → child sitemaps → URLs, incl. `.gz`),
link-BFS fallback when no usable sitemap, and well-known files (`llms.txt`,
`llms-full.txt`, `ai.txt`, `.well-known/security.txt`, `humans.txt`, favicon).
Same-site scoped; bounded by `MAX_PAGES`/`MAX_DEPTH`/`DELAY`.

**2 — TLS is gathered from third-party scanners only** (Qualys SSL Labs, urlscan),
because a TLS-intercepting egress proxy makes local handshake tools
(testssl/openssl/httpx `-tls-grab`) report the proxy cert, not the site's — so no
local handshake scan is run; it would add no signal about the target's real TLS.
SSL Labs gives the authoritative grade/protocols/cert, and urlscan.io independently
re-observes the real site certificate from its own (off-proxy) infrastructure
(§5.4), so the two corroborate each other. (crt.sh Certificate Transparency was
previously used here but proved persistently unreliable — frequent rate-limiting /
timeouts — and was dropped; SSL Labs + urlscan cover TLS authoritatively.)

**7 — SEO (`seo-check.py`, all pages).** Per page: title, meta description,
canonical, meta-robots (noindex), exactly-one-H1, html `lang`, viewport meta,
Open Graph, Twitter card, JSON-LD structured data, favicon, word count, images
missing `alt`. Cross-page: duplicate titles/descriptions. **Google indexability verdict per page** (HTTP status + meta-robots + `X-Robots-Tag` header + robots.txt Disallow + canonical self/cross) and **mixed-content** detection. Site-wide: broken
internal + external links (deduped; social bot-blocks separated).

**8 — Multi-page responsive (`qa-site.py`, all pages capped).** Per page at mobile
+ desktop: page horizontal scroll, crushed/uneven grid columns, edge-clipped
elements, hamburger size. Aggregates "template-wide" issues (same culprit class
across many pages) and screenshots only pages **with** issues (evidence, lean).

## Scaling model (large sites)
- **All-pages, cheap:** discovery (0), SEO + links (7), responsive regression (8).
  These are raw-HTML or layout-only and run on every page (bounded by caps).
- **Once per host:** DNS/TLS/headers/reputation (1–4) — host-level, not per page.
- **Deep + expensive, sampled:** image/spelling/marquee/theme deep dive (6) runs on
  the homepage (assets/templates repeat, so this is representative). Raise coverage
  with `QA_MAX_PAGES` for stage 8 if you want more pages in the layout regression.
- Tune `MAX_PAGES`, `SEO_MAX_PAGES`, `QA_MAX_PAGES` to trade coverage for runtime.

## Prerequisites
`apt install dnsutils jq curl aspell aspell-en whatweb bsdextrautils poppler-utils imagemagick` ·
`pip install shot-scraper Pillow && shot-scraper install` · `npm i -g lighthouse` ·
`go install .../httpx@latest .../nuclei/v3/cmd/nuclei@latest` · Playwright Chromium.
(The web `SessionStart` hook installs all of the above and runs a preflight self-test.)
Each stage degrades gracefully if a tool is missing.

## Validation
Discovery (recursive sitemap, link-BFS, AI-bot policy, caps) and the multi-page
stages were validated against a local 5-page fixture with a sitemap index and
robots AI rules, in addition to the live target.

## Stages 9–10 detail
**9 — Accessibility (`a11y-check.py`).** axe-core (injected from CDN) per page,
violations grouped by impact. **Independent cross-check:** contrast ratios are also
computed directly from the rendered DOM via the WCAG luminance formula, so a
low-contrast finding is confirmed by a second method, not just axe.

**10 — Performance (`perf-check.py`).** Prefers Google PageSpeed Insights (Lighthouse
on Google's servers — representative timing + CrUX field data). Falls back to local
Lighthouse. **Honesty rule:** local timing/score run through this environment's proxy
from a datacenter and are marked *indicative only*; the trustworthy outputs are the
structural opportunities (image sizing/dimensions, render-blocking, compression,
modern formats) and the a11y/best-practices/SEO categories.

## Standards & best-practice citations (tests *and* educates)
Every check is backed by `standards.py` — a single knowledge base mapping each
finding to (a) the authoritative spec/clause that defines it, (b) the governance
framework(s) it rolls up to, and (c) a plain-English rationale for **why a result
passes or fails**. The check scripts attach a `standards` block to their JSON
(`5-seo.json`, `8-a11y.json`, `9-perf.json`) and a `WHY / STANDARDS` section to
their `.txt`; the report and `REMEDIATION-PROMPT.md` quote these so each finding
explains itself.

| Domain | Authoritative standard cited | Governance frameworks mapped |
|---|---|---|
| Accessibility | WCAG 2.2 success criteria, WAI-ARIA APG | EN 301 549, Section 508, ADA |
| Security headers | OWASP Secure Headers, RFC 6797 (HSTS), W3C CSP, RFC 7034 | NIST CSF 2.0, ISO 27001:2022, CIS Controls v8 |
| TLS | Mozilla Server Side TLS, NIST SP 800-52r2, CA/B Forum BR | PCI DSS 4.0, NIST CSF 2.0, ISO 27001:2022, CIS v8 |
| DNS | RFC 8659 (CAA), RFC 4033-4035 + NIST SP 800-81 (DNSSEC) | NIST CSF 2.0, ISO 27001:2022, CIS v8 |
| Email auth | RFC 7208 (SPF), RFC 7489 (DMARC), RFC 6376 (DKIM) | CIS v8 9.5, ACSC email hardening / Essential Eight context |
| SEO / indexability | Google Search Essentials, Schema.org, RFC 6596, RFC 9309 | — |
| Performance | Google Core Web Vitals (LCP/CLS/INP), web.dev | Google page-experience |

Honesty rule applies here too: where a framework genuinely does not govern a
control (e.g. ACSC Essential Eight is endpoint-focused and does not cover web
response headers), the KB says so rather than forcing a bad mapping.

Inspect or look up any check directly:
```bash
python3 standards.py                       # dump the whole KB as JSON
python3 standards.py --list                # one line per check id
python3 standards.py hsts color-contrast   # cite specific checks (standard + frameworks + why)
```

## Multiple verification of negative findings
Every negative finding is corroborated by ≥2 independent sources:

| Negative finding | Source 1 | Source 2 | Source 3 |
|---|---|---|---|
| Missing security headers | `curl` direct | Mozilla Observatory (their servers) | securityheaders.com |
| Real TLS / weak protocol | Qualys SSL Labs (grade) | urlscan (off-proxy cert) | — |
| Low colour contrast | axe-core (stage 9) | WCAG luminance calc (stage 9) | Lighthouse a11y (stage 10) |
| Images missing width/height (CLS) | qa-check (stage 6) | Lighthouse `unsized-images` (stage 10) | — |
| Broken links | qa-check (stage 6) | seo-check site-wide (stage 7) | — |
| SEO gaps | seo-check (stage 7) | Lighthouse SEO (stage 10, basics) | — |

TLS is intentionally NOT trusted from local handshake tools (proxy interception);
performance timing is intentionally NOT trusted locally (proxy+datacenter).
