# Audit Report Template — formatting contract

This is the **authoritative spec** for the PDF audit report. The report builder
(`report_lib.py` + a per-site `build_report.py`) MUST follow it exactly so every
report is consistent. Do not improvise structure or styling — change *this file*
and the shared library, never one report in isolation.

The golden rule: **every claim shows its working.** A finding states what was
measured, shows the evidence (annotated screenshot / metric / header), gives the
fix, and cites the standard. Anything the pipeline could not measure is **flagged**,
never silently dropped.

---

## 1. Page + type styles (single source of truth: `report_lib.CSS`)

| Element | Rule |
|---|---|
| Page | A4, margin 15mm 13mm. |
| Body font | Helvetica Neue / Arial, 10.5pt, line-height 1.5, colour `#1a1f2b`. |
| H1 | 25pt, `#0b3d2e`. H2 | 15pt, `#0b3d2e`, 2px green underline, `page-break-after:avoid`. H3 | 11.5pt, `#13334f`. |
| Mono / `code` | 8.8pt, light-grey background `#eef1f5`. |
| **Italics** | **NEVER** for a paragraph or sentence. Italics are banned except inside verbatim quoted page copy. Emphasis uses **bold**. |
| Page flow | Each top-level section (`h2`, §2 onward) starts on a new page via `page-break-before:always`; **§1 is the exception** — it shares the cover page (`h2:first-of-type` cancels the break) so the title page isn't left blank. `figure`, tables, `.sf`, callouts use `page-break-inside:avoid`. |

### Colour + badge taxonomy (fixed meanings)
| Badge | Class | Meaning |
|---|---|---|
| EXCELLENT / STRONG / CLEAN / GOOD | `good` (green `#16a34a`) | Meets or exceeds best practice; no action. |
| IMPROVE / HARDEN | `med` (amber `#d4a017`) | Works, but a best-practice gap to close. |
| NEEDS FIX | `high` (orange `#e67e22`) | A real defect users/search engines hit. |
| CRITICAL | `crit` (red `#c0392b`) | Security/availability risk; fix now. |
| OPT / INFO | `info` (blue `#2980b9`) | Optional hardening / informational. |

### Component styles
- **Tables**: `table-layout:fixed`; ALWAYS define `<colgroup>` widths; header row dark-green; zebra even rows. No raw unstyled tables.
- **Figure**: bordered, `page-break-inside:avoid`, left-aligned caption ≤ 1 line of context.
- **`.sf` (Standards & frameworks)**: light-blue left-border box, placed **immediately under the fix** of the section it belongs to (never a separate appendix). Heading "STANDARDS & FRAMEWORKS REFERENCED"; one `<li>` per standard with the framework roll-up and the reference URL. Sourced from `standards.py`.
- **`.fix`**: green left-border box, starts with bold "Fix:" (or "No action.").
- **`.caveat`** (amber) for measurement-honesty notes; **`.keyfind`** (red) for the exec-summary issues list. Callouts are boxes, never italic paragraphs.

---

## 2. Section order (FIXED — every report, in this order)

| # | Section | Required content | Data source |
|---|---|---|---|
| 1 | Executive summary | One-paragraph context; a `.keyfind` box listing the issues found; a verdict table (Area / badge / headline); a `.caveat` measurement-honesty note. | author + all stages |
| 2 | **Run status & coverage** ("show working") | A status table of EVERY stage: `ok / failed / skipped` + detail. Explicitly call out anything not fully measured (e.g. urlscan skipped = no API key; whois failed; PSI absent → perf timing indicative). | `0-run-status.json` |
| 3 | Site map | A node diagram of all crawled URLs + count + indexability summary. | `crawl/urls.txt`, `5-seo.json` |
| 4 | Technology stack | Wappalyzer-style table: Layer / Technology / Evidence. | `1-httpx.json`, headers, `dom.html`, local capture |
| 5 | Security | 5.1 TLS (SSL Labs, note proxy caveat), 5.2 headers, 5.3 vulnerability scan (nuclei), **5.4 malware / external reputation (urlscan.io, with the scan screenshot)**. Each sub-finding: badge + fix + `.sf`. | `2-tls-*`, `3-headers.txt`, `3-nuclei.txt`, `4-*`, `urlscan.png` |
| 6 | Responsive & UI | Functional bugs first, each with an **annotated/circled** screenshot. Tap targets. | `6-qa.json`, `7-qa-site.json`, screenshots |
| 7 | Accessibility | axe summary; genuine contrast failures (annotated) vs **indeterminate** (gradient/image bg — flagged "visual check", NOT counted as fail). | `8-a11y.json` |
| 8 | Performance | Category scores; structural findings (CLS, unsized images); timing marked indicative if local. | `9-perf.json` |
| 9 | SEO / indexability | Indexability verdict; metadata gaps; broken links (403/503 marked "verify — may be bot-block"). | `5-seo.json` |
| 10 | Email / DNS | SPF/DMARC/DKIM, CAA, DNSSEC. | `1-dns.txt` |
| 11 | Prioritised remediation | Table: # / priority badge / item / effort. Ordered by severity. | author |
| 12 | Methodology | A table mapping each area/§ to its tool + vantage (Local vs External), plus a short honesty note (standards KB, run-status, ≥2-source corroboration). | author + `0-run-status.json` |

Sections 2, 3, 4, 11, 12 are **generated deterministically** by `report_lib` from the
artifacts — no per-site authoring. Sections 5–10 are authored narrative that MUST use
the shared components (badge/table/fig/sf/fix) so formatting cannot drift.

---

## 3. Quality rules (deterministic — enforced in code, not by judgement)

1. **No silent failure.** Section 2 reflects `0-run-status.json` verbatim. If a stage
   is `failed`/`skipped`, the affected finding must say so (e.g. "external reputation
   not obtained — urlscan needs an API key").
2. **Genuine vs indeterminate contrast.** Use `contrast_summary.genuine_failures` for
   the headline count. Gradient/image-background runs go in a separate "needs visual
   check" line and are NEVER reported as failures or as 1:1 ratios.
3. **Timing honesty.** If perf source is local Lighthouse, label LCP/FCP/Speed-Index
   "indicative only (datacenter+proxy)". Structural audits (CLS, unsized images) and
   category scores are reliable.
4. **TLS provenance.** Always state TLS came from SSL Labs + urlscan.io (their own
   off-proxy handshake); never quote httpx `-tls-grab` as the site's cert (it sees the proxy).
5. **Broken links.** 403/503 from gov/social hosts are marked "verify — likely bot
   rate-limiting from the audit IP", separated from true 404s.
6. **Every finding cites a standard** via `standards.py`; if a check has no KB entry,
   add one to `standards.py` rather than hand-writing an uncited claim.
7. **Thresholds that escalate to a visual/AI pass** (flagged in Section 2 and the
   relevant finding): any `contrast_indeterminate > 0`; any stage `failed`; any
   responsive horizontal-scroll bug; images displayed < 50% of natural size.

---

## 4. How to regenerate

```bash
./run-audit.sh https://target            # writes ./audit/ incl. 0-run-status.json
python3 report-assets/annotate.py        # circle the flagged issues -> annot-*.png
python3 report-assets/build_report.py    # follows this template -> AuditReport-*.pdf
```
The builder imports `report_lib` (canonical CSS + components) and `standards.py`
(citations). Changing the look = edit `report_lib.CSS` + this file, once.
