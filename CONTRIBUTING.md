# Contributing

Thanks for your interest in improving the Website Audit Toolkit. This guide covers how
to set up, the non-negotiable rules that keep reports trustworthy, and how to propose
changes.

## Ground rules

- **Authorized use only.** When testing changes, run the pipeline against sites you own
  or have written permission to audit (see [`SECURITY.md`](SECURITY.md) and
  [`THIRD-PARTY-TOOLS.md`](THIRD-PARTY-TOOLS.md)). Don't add example runs of third-party
  sites to the repo — `examples/` is gitignored on purpose.
- **Never commit secrets.** Keys come from the environment or a gitignored `.env`. Don't
  print key values in logs or write them into artifacts.
- **Be honest about measurement.** This toolkit's credibility rests on never claiming
  more than it measured (see the honesty rules below).
- **Be respectful of the hosted services.** Qualys SSL Labs, urlscan.io and Google PSI
  have their own rate limits and terms — don't add code that hammers them.

## Dev setup

Install the prerequisites (see the **Setup from scratch → Option B** section of
[`README.md`](README.md)), then:

```bash
./run-audit.sh https://a-site-you-own.example   # collectors self-test in preflight
python3 standards.py --list                      # sanity-check the citations KB
```

On Claude Code on the web the `.claude/hooks/session-start.sh` hook installs everything
and self-tests at session start.

## Coding standard & local checks

The standard is enforced by tooling, not preference — run these before pushing (CI runs
the same):

```bash
pip install ruff                     # or: pipx install ruff
ruff check .                         # lint (config in pyproject.toml)
ruff format .                        # auto-format (beautify) to the house style
python3 -m py_compile *.py           # every collector must byte-compile
shellcheck --severity=warning run-audit.sh .claude/hooks/session-start.sh
```

Conventions (codified in [`pyproject.toml`](pyproject.toml)):

- **Python**: ruff lint set `E/F/W/I/UP/B`, line length **120**, double-quoted strings,
  sorted imports, target 3.11. `ruff format` is the single source of truth for layout —
  don't hand-format.
- **Deliberate exception:** the per-stage collectors use compact paired statements and
  guard clauses (`if x: return`, `a; b`) consistently. This is an intentional, internally
  consistent convention for these single-purpose scripts, so `E701`/`E702` are disabled.
  Keep new code in the same terse style; don't mix idioms within a file.
- **Shell**: must pass `shellcheck` at warning level; keep `set -uo pipefail`.
- **No dead code.** Unused imports/variables/helpers fail lint — remove them, don't
  comment them out.

## Continuous integration & security scanning

Every push/PR runs (see [`.github/workflows/`](.github/workflows)):

- **CI** — ruff lint + format check + byte-compile, and ShellCheck.
- **Semgrep** — SAST over Python, Bash and secret patterns (`p/python`, `p/bash`,
  `p/secrets`), failing on any finding.
- **Dependabot** ([`.github/dependabot.yml`](.github/dependabot.yml)) — weekly updates for
  the pip runtime deps and the GitHub Actions.

Two more are enabled in the repository settings (no workflow file needed), recommended
before going public:

- **CodeQL** code scanning — turn on GitHub *Code scanning → Default setup* (free for
  public repos).
- **Secret scanning + push protection** — GitHub *Settings → Code security*.
- **Socket** — install the **Socket Security GitHub App** for dependency/supply-chain
  review on PRs (zero-config; no token in CI).

## The two hard rules

1. **Formatting cannot drift.** All report structure and styling live in
   [`report_lib.py`](report_lib.py); the contract is [`REPORT-TEMPLATE.md`](REPORT-TEMPLATE.md).
   If you change one, change the other **in the same PR**. Never hand-roll CSS or tables
   in a per-site `build_report.py` — reuse `badge` / `table` / `callout` / `fig` / `sf` / `fix`.
2. **Every finding shows its working and cites a standard.** New checks must add their
   citation (authoritative standard + governance framework + plain-English why) to
   [`standards.py`](standards.py), and the report must surface evidence (metric, header,
   or annotated screenshot), the fix, and the citation — never a bare assertion.

### Honesty rules (enforced in review)
- TLS/reputation come only from third-party scanners that handshake from their own
  servers (SSL Labs, urlscan.io) — never quote a local handshake (proxy) as the site's cert.
- Local Lighthouse **timing** is indicative only; its scores and structural audits are reliable.
- Contrast: use `contrast_summary.genuine_failures` for headline counts; gradient/image
  backgrounds are "needs visual check", never counted as fails or quoted as 1:1.
- A stage that was `skipped`/`failed` must be reported as such — nothing silently dropped.

## Proposing changes

1. Open an issue describing the change (bug, new check, doc fix) before large PRs.
2. Keep PRs focused; explain **what was measured** and **why** for any new finding type.
3. For a new collector stage: write artifacts to `./audit/`, record `ok/failed/skipped`
   in `0-run-status.json`, and degrade gracefully if an optional tool is missing.
4. Match the surrounding code style; no new heavyweight dependencies without discussion.

## Licensing of contributions

This project is licensed under [MIT](LICENSE). By submitting a contribution you agree it
is licensed under the same terms. Contributions remain the copyright of their authors.
