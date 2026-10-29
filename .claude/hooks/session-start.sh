#!/bin/bash
# SessionStart hook for Claude Code on the web.
# Installs the Website Audit Toolkit prerequisites so future web sessions can run
# the pipeline and its checks. Idempotent and non-interactive.
#
# Core deps (required, fail the hook if they break): system packages, the Python
# QA stack (playwright + chromium, shot-scraper, Pillow).
# Optional deps (best-effort, never fail the hook): Go scanners (httpx, nuclei),
# lighthouse — every stage in run-audit.sh degrades gracefully when these are absent.
set -euo pipefail

# Only run inside the remote (Claude Code on the web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  echo "session-start: not a remote session, skipping toolkit install."
  exit 0
fi

echo "session-start: installing Website Audit Toolkit prerequisites..."

# --- System packages (Stages 0-5; whatweb = stage-3 tech fingerprint) ---
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  dnsutils whois jq curl aspell aspell-en poppler-utils imagemagick whatweb bsdextrautils \
  || sudo apt-get install -y dnsutils whois jq curl aspell aspell-en poppler-utils imagemagick whatweb bsdextrautils

# --- Python QA stack (Stages 5-10) ---
# The cloud image preinstalls Chromium in /opt/pw-browsers for its global Node Playwright. An
# unpinned pip install pulls a newer Python Playwright that expects a different Chromium revision,
# so no browser stage can launch. Match the Python package to that Node version when present
# (tracks the image instead of a hard pin, which would go stale); otherwise take the latest.
PW_SPEC="playwright"
if [ -d /opt/pw-browsers ] && command -v npm >/dev/null 2>&1; then
  node_pw="$(node -p "require('$(npm root -g)/playwright/package.json').version" 2>/dev/null || true)"
  [ -n "$node_pw" ] && PW_SPEC="playwright==${node_pw}"
fi
echo "session-start: installing ${PW_SPEC}"
pip install --no-input "$PW_SPEC" shot-scraper Pillow \
  || pip install --no-input playwright shot-scraper Pillow
if [ -d /opt/pw-browsers ]; then
  export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers
  [ -n "${CLAUDE_ENV_FILE:-}" ] && echo "export PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers" >> "$CLAUDE_ENV_FILE"
fi
# A no-op when the matching revision is already present. If the download is blocked, run-audit.sh
# preflight falls back to any preinstalled Chromium (PW_CHROMIUM_EXECUTABLE) and records it.
playwright install --with-deps chromium \
  || echo "session-start: WARN playwright browser install failed (preflight will try a preinstalled Chromium)"
shot-scraper install || echo "session-start: WARN shot-scraper browser install failed"

# --- Scanners (Stage 1/3 + perf): install all so nothing is silently absent ---
GOBIN_DIR="$(go env GOPATH)/bin"
go install github.com/projectdiscovery/httpx/cmd/httpx@latest \
  || echo "session-start: WARN httpx install failed (stage 1 degrades gracefully)"
go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest \
  || echo "session-start: WARN nuclei install failed (stage 3 degrades gracefully)"
# nuclei ships no templates; fetch them now. env -u GITHUB_TOKEN: the updater hits the GitHub
# API and an unrelated GITHUB_TOKEN in the environment makes it 401. Without templates stage 3
# aborts with "no templates provided for scan".
env -u GITHUB_TOKEN -u GH_TOKEN "${GOBIN_DIR}/nuclei" -update-templates -silent \
  || echo "session-start: WARN nuclei template update failed (stage 3 degrades gracefully)"
npm install -g lighthouse \
  || echo "session-start: WARN lighthouse install failed (stage 10 performance unavailable)"

# Make Go-installed scanners discoverable for the rest of the session.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"\$PATH:${GOBIN_DIR}\"" >> "$CLAUDE_ENV_FILE"
fi
export PATH="$PATH:${GOBIN_DIR}"

# --- Self-test: confirm every prerequisite is present before sessions rely on it ---
echo "session-start: prerequisite self-test —"
missing=0
for t in python3 dig curl jq whois httpx nuclei whatweb shot-scraper aspell node lighthouse pdftoppm convert; do
  if command -v "$t" >/dev/null 2>&1; then printf '  [ok]      %s\n' "$t"; else printf '  [MISSING] %s\n' "$t"; missing=$((missing+1)); fi
done
python3 -c 'import playwright, PIL' 2>/dev/null && echo "  [ok]      python: playwright + Pillow" \
  || { echo "  [MISSING] python: playwright/Pillow"; missing=$((missing+1)); }
[ "$missing" -eq 0 ] && echo "session-start: all prerequisites present." \
  || echo "session-start: WARN $missing prerequisite(s) missing — run-audit.sh preflight will also report them."

echo "session-start: done."
