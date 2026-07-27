# Changelog

## 2026-07-24 — Public-release readiness

Final cleanup before opening the repository publicly.

- **Filled the community-health contacts.** `SECURITY.md` and `CODE_OF_CONDUCT.md` no
  longer carry `[fill in]` placeholders — both now route reports to the repo's private
  GitHub Security Advisories channel.
- **Redacted real client references** from earlier changelog entries. Worked audits run
  against real third-party sites, so their domains are no longer named here; entries now
  refer to "the sample audit" / "a live client site."
- **Cleaned the git history.** The previously bundled `examples/` (real client audit
  output and PDF reports), removed from the working tree earlier, was also purged from
  git history so a public clone cannot recover it.

## 2026-06-17 — Fix DNSSEC detection (authoritative, not AD-flag)

- **`run-audit.sh` DNS stage** previously inferred DNSSEC from the `dig` response&#39;s
  `AD` (authenticated-data) flag, which is resolver-dependent — a non-validating
  resolver never sets it and it can match spuriously — so it false-reported unsigned
  zones as `DNSSEC:on`. Now determined authoritatively from the records themselves:
  `DNSKEY` present = zone signed, `DS` at the parent = anchored. Reports
  `off (unsigned)` / `partial (signed, no DS)` / `on`, and writes the DNSKEY/DS query
  output into `1-dns.txt`. Verified against a known-unsigned zone → off (correct) and a
  known-signed zone (`cloudflare.com`) → on.

## 2026-06-17 — Open-repo prep: licensing, third-party terms, examples removed

Groundwork for publishing this as an open repository.

- **Added community health files:** `SECURITY.md` (authorized-use + private vulnerability
  reporting for the toolkit's own code), `CONTRIBUTING.md` (dev setup + the two hard
  rules: formatting can't drift, every finding shows its working and cites a standard),
  and `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1). README links all three.
  NOTE: `SECURITY.md` and `CODE_OF_CONDUCT.md` have a `[fill in]` contact placeholder.

- **Added `LICENSE` (MIT).** Covers only this repo's own code/docs. MIT matches the
  majority licence among the tools the toolkit invokes (most are MIT/Apache/BSD-style
  permissive; MIT is the plurality), and is the simplest permissive choice.
- **Added `THIRD-PARTY-TOOLS.md`** — a full inventory of every tool (upstream + usual
  licence + role), the runtime-fetched axe-core (MPL-2.0 via jsDelivr), and the hosted
  services with their own Terms of Use (Qualys SSL Labs, urlscan.io, optional Google
  PSI, rdap.org). Includes an **authorized-use-only** disclaimer (active scanning vs
  computer-misuse law), a trademarks/non-affiliation note, and a no-warranty clause.
- **Removed the bundled `examples/` from the repo.** Worked runs audit real third-party
  sites, so they are no longer published; `examples/` is now gitignored (local copies
  kept privately). Docs that pointed at the example `report-assets/` now point at the
  `REPORT-TEMPLATE.md` contract + `report_lib.py` instead. NOTE: the examples still exist
  in prior git history — a clean public release would need a fresh repo or history rewrite.
- **README:** added a "Legal, licensing & authorized use" section and replaced the
  Examples section with a "Producing a report" walkthrough.

## 2026-06-17 — Report feedback: drop crt.sh, malware subsection, methodology table

Round of report-feedback fixes on the sample audit.

- **§1 shares the cover page.** The first `h2` no longer forces a page break
  (`h2:first-of-type` in `report_lib.py`), so the executive summary sits directly under
  the title instead of leaving the cover mostly blank. §2–§12 still page-break as before.
- **Dropped crt.sh / Certificate Transparency entirely.** It proved persistently
  unreliable (rate-limiting / timeouts) and added nothing SSL Labs + urlscan don't already
  cover — both handshake from their own infrastructure and urlscan re-observes the real
  site cert off-proxy. Removed the stage from `run-audit.sh` (and its now-unused
  `fetch_json_retry` helper), the `tls_crtsh` load in `report_lib.py`, all report/§2/§5.1
  references, and the stale `2-tls-crtsh.*` artifacts. README/PROCESS/REPORT-TEMPLATE updated.
- **Removed the lingering `testssl` references.** The stage was already gone; cleaned up the
  remaining mentions in the report's §2 caveat and the `run-audit.sh` comments.
- **Malware scan is its own subsection (§5.4) again, with the screenshot.** urlscan.io's
  external render + threat verdict moved out of §5.3 into a dedicated "Malware & external
  reputation" subsection, and the `urlscan.png` scan screenshot is shown again. §5.3 is now
  the nuclei vulnerability scan only.
- **Methodology (§12) is now a table** (area / § / tool & method / vantage + reliability)
  instead of one dense paragraph, with a short honesty note beneath it.

## 2026-06-17 — TLS simplification + report pagination

Re-ran the full pipeline against a live client site with a live
`URLSCAN_API_KEY` and refreshed the sample audit.

### TLS: drop the local `testssl` stage
- **Removed `testssl` from the pipeline.** Behind a TLS-intercepting egress proxy a
  local handshake can only ever observe the proxy's cert, not the site's, so the
  stage produced no signal about the target's real TLS — it was always recorded as a
  "failed (expected)" row, which was just noise. Authoritative TLS now comes solely
  from third parties that handshake from their own infrastructure: **Qualys SSL Labs**
  (grade/protocols/cert) + **crt.sh** (Certificate Transparency), **cross-confirmed by
  urlscan.io's external vantage** (§5.3 — it observed the real Google Trust Services
  cert from outside the proxy). Removed `testssl.sh` from `run-audit.sh` preflight, the
  README/PROCESS prerequisites, and the `SessionStart` install hook + self-test.
- The report's §2 coverage note now explains the only remaining TLS degradation path —
  a transient crt.sh outage — and states plainly that no local testssl scan is run and
  why; SSL Labs + urlscan keep TLS authoritative regardless.

### Report: one section per page
- Each top-level section (§1–§12) now starts on its own page (`page-break-before` on
  `h2` in `report_lib.py`), with the cover as a dedicated title page — easier to read
  and navigate than the previous continuous flow.

## 2026-06-16 — Verification & hardening pass

End-to-end verification of the toolkit against a live client site, plus fixes for
every gap found. The regenerated sample audit was kept locally (under the gitignored
`examples/` dir), not committed.

### Pipeline reliability
- **Run-status manifest.** Every stage now records `ok` / `failed` / `skipped`
  with a reason to `0-run-status.{txt,json}`, so nothing fails silently. The PDF
  report's §2 renders this table and derives its coverage caveat from it (the
  caveat can no longer go stale relative to the actual run).
- **Preflight self-test.** `run-audit.sh` checks every required tool/library at
  startup and aborts if a core one is missing (`PREFLIGHT_STRICT=0` to override).
  The web `SessionStart` install hook runs the same self-test after installing.
- **Full prerequisites in the install hook** — added `whatweb`, `testssl.sh`,
  and `bsdextrautils` (hexdump) so a fresh cloud session has the complete toolkit.

### Methodology fixes (running behind a TLS-intercepting egress proxy)
- **whois → RDAP over HTTPS.** Port 43 is blocked in the sandbox; registration
  data now comes from `rdap.org` over 443 (`1-rdap-*.json`, summarized into
  `1-whois.txt`). Returns more than whois did: registrar, registration/expiry
  events, status, nameservers, and the IP network/range.
- **TLS is proxy-safe.** In-container tools only ever see the egress-proxy cert,
  so authoritative TLS comes from third parties that handshake from their own
  servers: crt.sh (Certificate Transparency) + Qualys SSL Labs, and — when a key
  is present — urlscan.io. `2-testssl` is expected to "fail" (it sees the proxy
  cert) and is labelled as such rather than reported as a site problem.
- **crt.sh backoff retry.** `fetch_json_retry()` adds exponential-backoff retry
  (2s/4s/8s) for the rate-limit-prone crt.sh endpoint; non-JSON/empty responses
  count as failures. If it still fails, SSL Labs remains the authoritative source.
- **Performance.** Local Lighthouse is documented as the standard method; scores
  and structural audits are reliable, timing is labelled indicative only (no
  real-user/field data). Removed the obsolete "set PSI_KEY" nagging throughout.

### Secrets
- `run-audit.sh` auto-loads a **gitignored `.env`** (precedence: real environment
  > `./.env` > toolkit-dir `.env`). Added `.env.example`, `.gitignore` entries,
  and a README *Secrets / API keys* section. Preflight reports key presence
  without printing the value.
- **Note:** Claude Code on the web has no dedicated secrets store yet —
  environment variables are visible to anyone who can edit the environment. For
  durable use, set `URLSCAN_API_KEY` in the environment config (not `.env`), and
  treat such keys as "visible to environment editors," rotating if exposed.

### urlscan.io external vantage (Stage 4)
- With a real `URLSCAN_API_KEY`, Stage 4 submits a scan and folds the independent
  external vantage into the report (§5.3): clean threat verdict, the **real site
  certificate observed from outside the proxy** (corroborating SSL Labs A+ and
  closing the proxy-cert blind spot), the third-party request/domain map, and
  cookies. Without a key it falls back to a local Playwright capture and says so.

### Report accuracy
- **JSON-LD correction.** An earlier draft claimed "no JSON-LD anywhere"; the
  whatweb cross-check exposed this as wrong. JSON-LD is in fact present and
  well-formed on 6 of 13 pages (homepage `ProfessionalService`, all four articles
  `BlogPosting`+`Organization`, how-it-works `FAQPage`). §9 now computes coverage
  directly from `5-seo.json` and recommends extending structured data to the 7
  secondary pages — so the claim is data-driven and can't drift.
- **Contrast.** §8 separates genuine WCAG-AA contrast failures (solid background,
  33) from indeterminate gradient/image-background cases (27, flagged for visual
  check) rather than over-claiming.
- The report is composed from the shared `report_lib.py` components against
  `REPORT-TEMPLATE.md`; dynamic figures are read from artifacts at build time.

### Build order
`./run-audit.sh <url>` → `report-assets/annotate.py` → `report-assets/build_report.py`.
