# CLAUDE.md — operating manual for the Website Audit Toolkit

This file is auto-loaded by Claude Code. It tells you (the AI) how to run a full
website audit and produce the PDF report. Read it before starting an audit.

## What this repo is

A repeatable pipeline that audits any website end-to-end (security, TLS, SEO/
indexability, accessibility, performance, responsive/visual UI) and produces a
**client-ready PDF report**. The data collection is a script (`run-audit.sh`); the
**report is authored by you** from the artifacts, following a fixed formatting
contract. Your job is to drive the run, interpret the evidence, and build the report.

## The two halves of the job

1. **Collection (deterministic, the script does it).** `./run-audit.sh https://site`
   runs the collection stages and writes machine-readable artifacts to `$OUT_DIR`
   (default `./audit/`). You do not
   hand-edit these.
2. **Reporting (judgement, you do it).** You read the artifacts + screenshots,
   annotate the evidence, and compose a per-site `build_report.py` that imports the
   shared `report_lib.py` and follows `REPORT-TEMPLATE.md` exactly, then render the PDF.

## End-to-end workflow for a new site

```bash
# 1. Collect. Outputs land in ./audit/ (move/rename to a per-site dir if you keep it).
./run-audit.sh https://example.com
```

2. **Review the evidence.** Read `audit/0-run-status.json` first (what ran / what was
   skipped), then the stage `.txt`/`.json` artifacts and the screenshots. Never report
   a finding the artifacts don't support; if a stage was `skipped`/`failed`, say so.

3. **Set up the report assets.** Create `report-assets/` next to the artifacts with two
   scripts (the public repo ships no worked example — build them from the contract in
   `REPORT-TEMPLATE.md` and the components in `report_lib.py`):
   - `annotate.py` — draws boxes/labels on the flagged screenshots (CLS, tap targets,
     overflow, contrast) so the report **shows its working**. Outputs `annot-*.png`.
   - `build_report.py` — imports `report_lib`, loads the artifacts via `rl.load()`, and
     composes the authored sections. **All structure/styling comes from `report_lib`;
     never hand-roll CSS or tables.**
   (`report-assets/` lives under the per-site audit output, which is gitignored, so it is
   not committed to the public repo.)

4. **Compose the report** strictly per `REPORT-TEMPLATE.md` (fixed section order §1–§12,
   badge taxonomy, "every claim shows its working", honesty rules). Pull dynamic numbers
   (contrast counts, perf scores, SEO/JSON-LD coverage, run status) from the artifacts so
   the prose can never drift from the data.

5. **Render.**
   ```bash
   python3 report-assets/annotate.py          # produce annot-*.png evidence
   python3 report-assets/build_report.py      # writes AuditReport-<site>.pdf
   ```
   Spot-check the PDF (render pages to PNG) before handing it over.

6. **(Optional) remediation brief.** Fill `REMEDIATION-PROMPT.template.md` so the site's
   own developer/AI can action the fixes.

## Non-negotiable rules (from REPORT-TEMPLATE.md — enforce them)

- **Show the working.** Every finding states what was measured, shows evidence
  (annotated screenshot / metric / header), gives the fix, and cites the standard via
  `standards.py`. Nothing the pipeline couldn't measure is silently dropped — flag it.
- **Measurement honesty.** This toolkit usually runs behind a TLS-intercepting egress
  proxy. TLS/reputation come only from third-party scanners that handshake from their
  own servers (Qualys SSL Labs + urlscan.io). Local Lighthouse **timing** is indicative
  only; its scores and structural audits are reliable. State this in the report.
- **Genuine vs indeterminate contrast.** Use `contrast_summary.genuine_failures` for the
  headline; gradient/image-background runs are "needs visual check", never counted as
  fails or quoted as 1:1.
- **Formatting cannot drift.** Change `report_lib.py` + `REPORT-TEMPLATE.md` together,
  never one report in isolation. Reuse `badge/table/fig/sf/fix/callout`.
- **Don't commit secrets.** Keys come from env / a gitignored `.env`.
- **Pacing and caps are audit design.** `DELAY`, `MAX_PAGES`, `QA_MAX_LINKS`, `SEED_URLS`
  and `HOST_STAGES` decide the load on someone else's site and what was covered. Choose them
  deliberately, and report anything listed as not checked (cap) or skipped.
- **Report, never apply.** The toolkit only observes. Don't change a target's
  configuration, data or auth settings as part of an audit.

## Where things live

| Path | Purpose |
|---|---|
| `run-audit.sh` | Orchestrates the collection stages → `$OUT_DIR` (default `./audit/`) |
| `crawl.py` `seo-check.py` `a11y-check.py` `perf-check.py` `qa-*.py` `capture-local.py` `overfetch-check.py` `diff-runs.py` | Per-stage collectors |
| `netutil.py` | Shared request policy: `AUDIT_UA`, per-host pacing, link checking |
| `report_lib.py` | Shared report components + CSS (single source of truth for look) |
| `REPORT-TEMPLATE.md` | The formatting contract the report MUST follow |
| `standards.py` | Citations KB — every finding maps to a standard + governance framework |
| `PROCESS.md` | Full per-stage detail + scaling model |
| `THIRD-PARTY-TOOLS.md` | Tool/service licences + terms of use + authorized-use disclaimer |

## Running on Claude Code on the web vs locally

- **On the web:** the `.claude/hooks/session-start.sh` SessionStart hook installs every
  prerequisite and self-tests at session start — nothing to install. `URLSCAN_API_KEY`
  is optional; if used, set it in the environment config (prefer a network-secrets
  section where offered — plain environment variables are visible to anyone with access
  to the environment), never as a file.
- **Local CLI:** ensure the prerequisites in `README.md` are installed; `run-audit.sh`
  self-tests in preflight and aborts if a core tool is missing.

## Status file (`ops/status.json`)

An external status dashboard mirrors `ops/status.json` read-only on a schedule.
Shape: `schemaVersion`, `app`, `description`, `lastUpdated`, `session`, `sprint{name,focus}`, `tasks[]`
(AI work: `id`, `title`, `priority` Critical/High/Medium/Low, `status` pending/in_progress/blocked/done, `notes`),
`completedTasks[]` (same + `completedDate`), `humanActions[]` (`item`, `priority`, `linkedTask`, `dueDate`, `notes`),
`health{}`. **Update it at the end of every session that changes task state** — open a task, close one, add or
clear a human action — before the final commit. It is the only thing the dashboard sees. Seeded 2026-09-22.
