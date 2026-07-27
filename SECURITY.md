# Security Policy

## Authorized use first

This is an auditing toolkit. Several stages actively probe a target (HTTP requests,
technology fingerprinting, and a **nuclei** vulnerability-template scan). **Only run it
against websites you own or are explicitly authorized to audit.** Unauthorized scanning
may be unlawful and may breach the target's terms of service. See
[`THIRD-PARTY-TOOLS.md`](THIRD-PARTY-TOOLS.md) for the full disclaimer and the terms of
the third-party tools and services involved.

## Reporting a vulnerability in this toolkit

If you find a security issue in **this project's own code** (for example: a command
injection in the collectors, a path-traversal in the report builder, a secret being
logged or written to an artifact, or unsafe handling of a scanned site's content):

1. **Do not open a public issue.** Use GitHub's **private vulnerability reporting**
   (the repo's *Security → Report a vulnerability* tab), which keeps the report
   confidential until a fix is available.
2. If the private-reporting tab is unavailable, open a draft advisory directly at
   <https://github.com/streetshadows/website-audit/security/advisories/new> instead
   of filing a public issue.

Please include: affected file(s)/stage, steps to reproduce, impact, and any suggested
fix. We aim to acknowledge reports within a few days. There is no bug-bounty program.

Vulnerabilities in the **third-party tools** the toolkit invokes (curl, nuclei, httpx,
Playwright, etc.) or the **hosted services** it calls (Qualys SSL Labs, urlscan.io,
Google PSI) should be reported to those projects/vendors directly.

## Secrets

API keys are read from the environment or a **gitignored `.env`** — never commit them.
If you discover a key committed to history, treat it as compromised: rotate it and
remove it from history.
