# Third-party tools, services, licenses & terms of use

This toolkit is an **orchestrator**: it calls tools you install yourself and a few
hosted services over their public APIs. It does **not** bundle, fork, or redistribute
any of those tools' source or binaries — it shells out to whatever is installed on the
machine and fetches one library (axe-core) from a public CDN at runtime. That keeps the
toolkit's own licensing simple, but it means **you are responsible for complying with
each tool's and each service's own licence and terms of use** when you run it.

This document lists everything the pipeline touches so that is transparent. Nothing
here is legal advice — where it matters, follow the linked upstream terms.

---

## 0. Read this first — authorized use only

Several stages actively probe the target site (HTTP requests, technology
fingerprinting, and a vulnerability-template scan with **nuclei**). Running active
scans, fingerprinting, or automated crawling against systems **you do not own or do not
have explicit, written permission to test** may be unlawful in your jurisdiction
(e.g. the US Computer Fraud and Abuse Act, the UK Computer Misuse Act 1990, the
Australian Criminal Code Act 1995 s.477, and similar laws elsewhere), and may breach
the target's terms of service.

**Only run this toolkit against websites you own or are explicitly authorized to
audit.** You are solely responsible for how you use it and for the load it places on
the target and on the third-party services below. The toolkit is provided **as-is, with
no warranty** (see §6).

---

## 1. Local tools (you install these; invoked as prerequisites)

The toolkit calls these as external commands. They are **not redistributed** by this
repo — the user installs them from their OS package manager / language ecosystem, so
each tool's licence governs the user's own copy. Licences below are the projects' usual
terms; **confirm the exact licence and version your distro ships**, as packaging varies.

| Tool (stage) | Upstream | Usual licence | Role here |
|---|---|---|---|
| **Python 3** | python.org | PSF License (permissive) | Runs the `*.py` collectors + report builder |
| **Node.js** | nodejs.org | MIT (+ deps) | Runs Lighthouse |
| **Go toolchain** | go.dev | BSD-3-Clause | Builds/installs httpx + nuclei |
| **curl** (1,2,3,4) | curl.se | curl licence (MIT/X-style) | HTTP requests, headers, API calls |
| **dig** / `dnsutils` (1) | ISC BIND | MPL-2.0 | DNS records, DNSSEC AD flag |
| **whois** (1) | Debian `whois` (M. d'Itri) | GPL-2.0-or-later | Registration fallback (RDAP-over-HTTPS is primary) |
| **jq** (all) | jqlang | MIT-style (jq licence) | Parse JSON artifacts |
| **httpx** (1) | ProjectDiscovery | MIT | HTTP probe / fingerprint |
| **nuclei** (3) | ProjectDiscovery | MIT (templates: MIT) | Misconfig/headers/tech template scan |
| **whatweb** (3) | urbanadventurer/WhatWeb | GPL-2.0 | Technology fingerprint |
| **Lighthouse** (10) | Google (`GoogleChrome/lighthouse`) | Apache-2.0 | Performance / a11y / SEO category audit |
| **Playwright** + Chromium (4,6,8,9) | Microsoft | Apache-2.0 (Chromium: BSD-3-Clause +) | Headless browser: capture, QA, axe injection |
| **shot-scraper** (5) | Simon Willison | Apache-2.0 | Responsive screenshots |
| **Pillow** (6, annotate) | python-pillow | HPND (MIT-CMU/PIL licence) | Read/annotate screenshots |
| **aspell** + `aspell-en` (6) | GNU Aspell | aspell: LGPL-2.1; dict: permissive | Spell-check page copy |
| **poppler-utils** — `pdftoppm`/`pdftotext` | freedesktop poppler | GPL-2.0/GPL-3.0 | Render PDF pages to PNG for spot-checks |
| **ImageMagick** — `convert` | ImageMagick Studio | ImageMagick Licence (Apache-2.0-compatible) | Image conversion in annotate/QA |
| **util-linux** — `column` (`bsdextrautils`) | kernel.org util-linux | GPL-2.0 / BSD (per-util) | Pretty-print tables in `.txt` artifacts |

**Copyleft note (GPL/LGPL tools above — whois, whatweb, poppler, aspell, util-linux):**
because the toolkit only *invokes* them as separate programs and does not copy or link
their code, their copyleft terms are not triggered for this repo. If you later **bundle
or vendor** any of them into a distribution, those licences (and their source-offer
obligations) would apply — so keep them as installed prerequisites, not vendored copies.

---

## 2. Runtime-fetched component

| Component | Source | Licence | Note |
|---|---|---|---|
| **axe-core** `4.10.2` (stage 9) | Deque Systems, via the **jsDelivr** CDN (`cdn.jsdelivr.net/npm/axe-core`) | MPL-2.0 | Downloaded at run time and injected into each page to run the accessibility audit. Not redistributed in this repo. Pin/override the version with `AXE_VER`. If you prefer not to fetch from a CDN, vendor `axe.min.js` locally (MPL-2.0 requires you keep its licence header and offer modified MPL files). |

---

## 3. Hosted services / APIs (their own Terms of Use apply)

These are **external services**. You submit requests (and, for urlscan, the target URL)
to third parties who operate under their own terms, rate limits, privacy policies and
data-retention rules. Review each before relying on it — especially for commercial or
high-volume use.

| Service (stage) | What we send | Terms / cautions |
|---|---|---|
| **Qualys SSL Labs API v3** (2) — `api.ssllabs.com` | The target hostname, for a TLS handshake from Qualys' servers | Free public API governed by the **SSL Labs Terms and Conditions** — review them; they restrict bulk/abusive use and commercial redistribution of results, and the service may rate-limit or be withdrawn. We make a single analyze call per run and poll for the result. Docs/terms: <https://www.ssllabs.com/about/terms.html> and <https://github.com/ssllabs/ssllabs-scan/blob/master/ssllabs-api-docs-v3.md> |
| **urlscan.io API** (4) | The **target URL**, submitted as an `unlisted` scan, plus your `URLSCAN_API_KEY` | You are submitting a third party's URL to a third-party scanner. We default to **`visibility: unlisted`** (not in public search, but reachable by anyone with the scan link) — **never** submit sensitive/staging/authenticated URLs you don't want exposed, and use `private` (paid tiers) or skip the stage if in doubt. Subject to urlscan's Terms & Privacy and free-tier rate limits. Terms: <https://urlscan.io/docs/terms/> · API: <https://urlscan.io/docs/api/> |
| **Google PageSpeed Insights API** (10, opt-in) — `googleapis.com/pagespeedonline` | The target URL | **Only called if you set `PSI_KEY`.** The default is local Lighthouse and makes **no** Google call. If enabled, the **Google APIs Terms of Service** apply: <https://developers.google.com/terms> |
| **rdap.org** (1) — `rdap.org/domain`, `/ip` | The target domain / IP | Public RDAP redirector to the authoritative registry/RIR (replaces port-43 whois). Public read service; be reasonable with volume. |
| **jsDelivr CDN** (9) | — (downloads axe-core; see §2) | Free public open-source CDN. Terms: <https://www.jsdelivr.com/terms> |

> A **Certificate Transparency lookup via crt.sh** was used previously but has been
> removed (it was unreliable); SSL Labs + urlscan now cover TLS. No crt.sh call remains
> in `run-audit.sh`.

---

## 4. Reference URLs in `standards.py` (citations only — not called)

`standards.py` cites authoritative specs and guidance (W3C/WCAG, OWASP Secure Headers,
IETF RFCs, Google Search Central, web.dev, Mozilla Server-Side TLS, Schema.org,
Deque University, CA/Browser Forum, etc.). These URLs are **printed as references** in
the report so each finding maps to a standard — the pipeline does **not** fetch them at
run time, and no endorsement by those bodies is implied.

---

## 5. Trademarks & affiliation

Qualys®, SSL Labs®, urlscan.io, Google™/Lighthouse/PageSpeed Insights, ProjectDiscovery
(httpx, nuclei), Deque®/axe®, WhatWeb, Microsoft®/Playwright and all other names are the
trademarks of their respective owners. This project is **independent and not affiliated
with, endorsed by, or sponsored by** any of them. Names are used nominatively, only to
say which tool/service performs which step.

---

## 6. No warranty

This toolkit and its outputs are provided **"as is", without warranty of any kind**,
express or implied, including merchantability, fitness for a particular purpose, and
non-infringement. The authors and contributors are **not liable** for any claim, damage,
or other liability arising from its use, including any consequence of scanning a target
without authorization or of breaching a third-party service's terms. See the repo's
`LICENSE` for the governing terms of this project's own code.
