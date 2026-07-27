# Website Audit Toolkit

> **Canonical home.** This repository is the standalone, self-contained source of
> truth for the Website Audit Toolkit. It was extracted from its original parent
> repo (which no longer carries this code) and re-initialised with a clean history
> for public release. Develop here.

A repeatable, scalable website-audit pipeline. One command runs 11 stages —
security, TLS, SEO/indexability, accessibility, performance, and responsive/visual
UI — and writes machine-readable artifacts. An AI assistant (Claude Code) then turns
those artifacts into a **client-ready PDF report**. Designed to be **reliable over
fast**: sequential, bounded by caps, and works on a 1-page site or a large
multi-page site.

There are two halves to a full audit:

1. **Collection** — `run-audit.sh` gathers evidence into `./audit/` (deterministic).
2. **Reporting** — the AI reads the evidence and composes the PDF from the shared
   `report_lib.py`, following the `REPORT-TEMPLATE.md` contract (judgement).

If you only want the raw data, run step 1. If you want the report, drive both with
Claude Code — see [Using it with Claude Code](#using-it-with-claude-code).

---

## Setup from scratch

### Option A — Claude Code on the web (recommended, zero local install)

1. Push/connect this repository to [Claude Code on the web](https://code.claude.com/docs/en/claude-code-on-the-web).
2. Pick a network policy with **open outbound access** when you create the
   environment — the audit must reach the target site and third-party scanners.
3. That's it. The `.claude/hooks/session-start.sh` **SessionStart hook** installs every
   prerequisite (dig, jq, httpx, nuclei, whatweb, Playwright/Chromium, shot-scraper,
   Lighthouse, poppler, ImageMagick, …) and runs a self-test at the start of every
   session. `CLAUDE.md` is auto-loaded so the assistant already knows the workflow.
4. (Optional) Add `URLSCAN_API_KEY` as an **environment secret** in the environment
   config — never as a file in the repo. See [Secrets / API keys](#secrets--api-keys).

Then just tell Claude: *"Run a full audit of https://example.com and build the report."*

### Option B — Local machine (CLI)

```bash
git clone <this-repo> website-audit && cd website-audit

# Prerequisites (Debian/Ubuntu shown; equivalents on other OSes):
sudo apt install dnsutils whois jq curl aspell aspell-en poppler-utils imagemagick whatweb bsdextrautils
pip install playwright shot-scraper Pillow && playwright install chromium && shot-scraper install
go install github.com/projectdiscovery/httpx/cmd/httpx@latest
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
npm install -g lighthouse

# (optional) secrets
cp .env.example .env   # then edit .env — it is gitignored, never commit it

# Verify the box is ready, then run:
./run-audit.sh https://example.com
```

`run-audit.sh` runs a **preflight self-test** (every required tool + library; aborts
if a core one is missing — set `PREFLIGHT_STRICT=0` to scan anyway) and **auto-loads
secrets** from a gitignored `.env`. Each stage also degrades gracefully if an optional
tool is absent.

To install Claude Code itself for the AI-driven report step, see the
[Claude Code docs](https://code.claude.com/docs).

---

## Usage

### 1. Collect (raw data only)

```bash
./run-audit.sh https://example.com          # artifacts land in ./audit/

# tuning (all optional):
MAX_PAGES=500 MAX_DEPTH=5 DELAY=0.3 \
SEO_MAX_PAGES=500 QA_MAX_PAGES=40 QA_VIEWPORTS="390,768,1440" \
  ./run-audit.sh https://example.com
```

Read `audit/0-run-status.json` first — it records every stage as `ok / failed /
skipped` (+ reason), so a degraded run is explicit rather than silent.

### 2. Report (AI-authored PDF)

The report is **not** a fixed script — the assistant interprets the evidence and
composes a per-site builder. You author these two scripts per site from the
[`REPORT-TEMPLATE.md`](REPORT-TEMPLATE.md) contract and the components in
[`report_lib.py`](report_lib.py) (the public repo ships no worked example):

```bash
# inside the audit output dir, with a report-assets/ alongside the artifacts:
python3 report-assets/annotate.py        # box/label the flagged screenshots (evidence)
python3 report-assets/build_report.py    # imports report_lib, follows REPORT-TEMPLATE → PDF
```

- `build_report.py` imports [`report_lib.py`](report_lib.py) (the single source of truth
  for layout/CSS) and pulls dynamic numbers straight from the artifacts, so the prose
  can't drift from the data.
- The structure, section order, badges and honesty rules are fixed by
  [`REPORT-TEMPLATE.md`](REPORT-TEMPLATE.md). Change the template + library together,
  never one report in isolation.

### Using it with Claude Code

This repo is built to be driven by [Claude Code](https://code.claude.com/docs).
[`CLAUDE.md`](CLAUDE.md) is auto-loaded and contains the full operating manual for the
assistant. A typical session:

> **You:** Run a full audit of `https://example.com` and build the PDF report.

The assistant will: run `run-audit.sh`, review the artifacts and screenshots, create a
`report-assets/` dir (annotate evidence + author `build_report.py` from `report_lib`),
render the PDF, and spot-check it. You then review and request edits (e.g. *"pull §1
onto the cover page"*, *"make methodology a table"*) until the report is right.

> On the **web**, prerequisites and `CLAUDE.md` are loaded automatically. On the **local
> CLI**, run `claude` from the repo root so `CLAUDE.md` is picked up, and make sure the
> Option B prerequisites are installed.

---

## Stages
| # | Stage | Script | Scope |
|---|-------|--------|-------|
| 0 | Discovery: robots.txt + AI-bot policy, recursive sitemap, link-BFS, well-known files | `crawl.py` | site |
| 1 | DNS / infra + **RDAP-over-HTTPS** registration (port-43-free) | `dig`,`curl` (RDAP),`httpx` | host |
| 2 | TLS (proxy-safe: SSL Labs, cross-confirmed by urlscan) | third-party APIs | host |
| 3 | Headers / misconfig / tech | `curl`,`nuclei`,`whatweb` | host |
| 4 | Reputation / external render | urlscan.io | host |
| 5 | Responsive screenshots | `shot-scraper` | homepage |
| 6 | Deep UI / image / spelling QA | `qa-check.py` | homepage (+ `QA_DEEP_PAGES`) |
| 7 | SEO / metadata / links / **Google indexability** | `seo-check.py` | all pages |
| 8 | Multi-page responsive regression | `qa-site.py` | all pages (capped) |
| 9 | Accessibility (axe-core + independent WCAG contrast) | `a11y-check.py` | pages (capped) |
| 10| Performance (local Lighthouse — standard method) | `perf-check.py` | pages (capped) |

See **[PROCESS.md](PROCESS.md)** for full per-stage detail, the scaling model, and the
"multiple verification of negative findings" matrix (every negative is corroborated
by ≥2 independent sources).

## Secrets / API keys
Keys are read from the environment, or auto-loaded from a **gitignored `.env`**
(`cp .env.example .env` and fill it in — `.env` is never committed). Real env vars
take precedence. On the web, set them as **environment variables / secrets in your
environment configuration** instead of a file
([docs](https://code.claude.com/docs/en/claude-code-on-the-web)). Preflight reports
which keys are detected (never printing the value).

- `URLSCAN_API_KEY` — enables the independent external reputation/render vantage
  (Stage 4) and the malware scan in report §5.4. Without it, Stage 4 is skipped and a
  local browser capture is used instead. Get one at <https://urlscan.io/user/profile/>.
- `PSI_KEY` — **not required.** Performance uses local Lighthouse as the standard
  method (category scores + structural audits are reliable; timing is indicative
  from a datacenter). Set this only if you specifically want PSI/CrUX field data.

## Standards & best practice (tests *and* educates)
Every check is backed by [`standards.py`](standards.py), a knowledge base that maps
each finding to the authoritative standard (WCAG 2.2, OWASP Secure Headers, the
relevant RFC, Google Search Essentials, Core Web Vitals) **and** the governance
framework it rolls up to (NIST CSF 2.0, ISO 27001:2022, CIS Controls v8, ACSC
Essential Eight, EN 301 549), plus a plain-English reason *why a result passes or
fails*. Scripts attach a `standards` block to their JSON/txt and the report quotes
it. Look up any check: `python3 standards.py hsts color-contrast` (or `--list`).

## Design principles
- **Don't trust the local network for what it can't measure.** This pipeline takes
  TLS and reputation from *third-party scanners that handshake from their own
  servers* (Qualys SSL Labs, urlscan) — a TLS-intercepting proxy would make
  local results wrong — and registration data via **RDAP over HTTPS** (not port-43
  whois, which firewalls block). Performance uses local Lighthouse, with its
  datacenter-dependent *timing* marked indicative while scores/structural audits stand.
- **Cross-verify negatives.** Missing headers (curl + Mozilla Observatory +
  securityheaders.com), contrast (axe-core + WCAG calc + Lighthouse), etc.
- **Geometry is necessary but not sufficient** for UI — always review the
  screenshots the tools flag.

## Producing a report
There is no bundled example audit in this public repo (worked runs audit real
third-party sites, so they are kept private). To see the full output, run the toolkit
against a site you own — `run-audit.sh` writes the artifacts, then the AI authors a
per-site `report-assets/` (an `annotate.py` + a `build_report.py` that imports
`report_lib.py`) and renders the PDF. The structure and rules are fixed by
[`REPORT-TEMPLATE.md`](REPORT-TEMPLATE.md); the shared components live in
[`report_lib.py`](report_lib.py). [`CLAUDE.md`](CLAUDE.md) walks the assistant through
the whole flow.

Use [`REMEDIATION-PROMPT.template.md`](REMEDIATION-PROMPT.template.md) as the starting
point for handing fixes to a site's own developer/AI.

## Contributing & security
Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup
and the two hard rules (formatting can't drift; every finding shows its working and
cites a standard). Please follow the [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). To
report a security issue in the toolkit itself, follow [`SECURITY.md`](SECURITY.md)
(use private vulnerability reporting — don't open a public issue).

## Legal, licensing & authorized use
**Authorized use only.** Stages 1–4 actively probe the target (HTTP requests, tech
fingerprinting, and a **nuclei** vulnerability-template scan). Only run this toolkit
against sites **you own or are explicitly authorized to audit** — unauthorized scanning
may be unlawful and may breach a target's terms of service. The toolkit is provided
**as-is, with no warranty**.

This repo is an **orchestrator**: it calls tools you install yourself and a few hosted
services (Qualys SSL Labs, urlscan.io, optionally Google PSI) over their public APIs. It
does **not** bundle or redistribute those tools. Each tool keeps its own licence and each
service its own terms of use — **you are responsible for complying with them**.
[`THIRD-PARTY-TOOLS.md`](THIRD-PARTY-TOOLS.md) documents every tool, its licence and
upstream, plus the API services' terms and the cautions that matter (e.g. urlscan
submits the target URL to a third party). See [`LICENSE`](LICENSE) for this project's own
code terms.

## Validation
Discovery (recursive sitemap, link-BFS, AI-bot policy, caps) and the indexability
verdict logic were validated against a local multi-page fixture and unit tests, in
addition to live runs.
