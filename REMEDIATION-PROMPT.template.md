# AI Remediation Prompt — TEMPLATE

After running `./run-audit.sh <url>`, copy the findings from `audit/*.txt` into the
task blocks below, then paste the whole thing into your AI coding assistant
(Claude Code, Cursor, etc.) **with the website's source repository open**.

> **Output:** save the filled-in copy as `<audit-dir>/REMEDIATION-PROMPT.md` (alongside
> the audit artifacts). The toolkit does not generate this file — it is authored from
> this template; the per-site audit dir is gitignored, so the brief stays private.

> **HOW TO USE:** Each fix is a self-contained `### TASK` block. **Delete any block
> you don't want applied** before sending — e.g. a flagged spelling may be an
> intentional brand choice, or a "missing" header may be set at a CDN/edge instead.
> Tasks marked *(non-code)* are content / DNS / asset actions to surface as
> instructions, not code edits.

---

You are a senior front-end + web-ops engineer. Apply the following fixes to this
website's source. For each task: make the change, keep the existing code style, and
report the file(s) touched. Do not make changes outside these tasks. After each code
task, briefly state how you verified it.

> **Cite the standard for every task.** Each stage's JSON/txt now carries a
> `standards` block (sourced from `standards.py`) mapping the finding to the
> authoritative spec (WCAG 2.2, OWASP Secure Headers, the relevant RFC, Google
> Search Essentials, Core Web Vitals) and the governance framework it rolls up to
> (NIST CSF 2.0, ISO 27001:2022, CIS Controls v8, ACSC Essential Eight, EN 301 549).
> Paste the matching `standard`/`frameworks`/`why` lines into each task below so the
> fix explains *why* it matters — the report should both test and educate. Look up
> any check directly with `python3 standards.py <check-id>`.

### TASK-SEC-headers — Add HTTP security headers *(if stage 3 / Observatory flags them)*
Add the missing response headers (HSTS, X-Content-Type-Options, X-Frame-Options,
Referrer-Policy, Content-Security-Policy, Permissions-Policy) in the server/CDN config.

### TASK-SEC-tls — Disable deprecated TLS *(if SSL Labs < A)*
Restrict to TLS 1.2 + 1.3 at the server.

### TASK-IDX-indexability — Fix non-indexable pages *(from stage 7 indexability)*
Resolve any `noindex` (meta or X-Robots-Tag header), robots.txt Disallow on content
pages, or cross-canonical that shouldn't be there.

### TASK-SEO-meta — Titles / descriptions / canonical / OG / JSON-LD *(from stage 7)*
Fix missing/duplicate/over-length titles & descriptions; add canonical tags, Open
Graph + Twitter cards, and JSON-LD structured data appropriate to the business.

### TASK-CRAWL-files — robots.txt / sitemap.xml / security.txt / favicon *(non-code, from stage 0)*
Add any missing site-hygiene files; decide AI-crawler policy in robots.txt.

### TASK-RESP-layout — Responsive layout bugs *(from stages 6/8)*
Fix crushed/uneven grids, edge-clipped elements, horizontal scroll at the flagged
viewport(s). Include the verified CSS where the report provides it.

### TASK-A11Y-contrast — Colour contrast & other axe violations *(from stage 9)*
Raise text/background contrast to ≥4.5:1 (≥3:1 large); fix other axe violations.

### TASK-PERF — Performance *(from stage 10, structural)*
Add explicit image width/height (CLS), resolve console errors, minify/compress,
serve right-sized/modern images.

### TASK-COPY — Copy / spelling *(from stage 6; review each — may be intentional)*
Apply the spelling/grammar corrections the report lists.

### TASK-IMG-assets — Image assets *(non-code, from stage 6)*
Re-export any mis-sliced/edge-bleeding strips and replace off-brand/oversized images.
