#!/usr/bin/env bash
# External website audit — run from any host with OPEN outbound network access.
# Usage: ./run-audit.sh [https://target]   (default: https://example.com)
# Optional: export URLSCAN_API_KEY=... (not required; anonymous scan is used if unset)
set -uo pipefail

TARGET="${1:-https://example.com}"
domain=$(echo "$TARGET" | sed -E 's#^https?://##; s#/.*##')
OUT="${OUT_DIR:-./audit}"; mkdir -p "$OUT"

# Every stage writes to a fixed filename, and several stages read their own output back to
# decide ok/failed (SSL Labs grade, dom.html, a11y/SEO counts). A file left by the previous run
# would be read as this run's result — e.g. an SSL Labs timeout reported ok with the old grade.
# So move the previous run's generated artifacts into $OUT/previous-run/ (replacing any older
# copy). Only files this pipeline writes are moved; report-assets/ and anything else is untouched.
rotate_previous_run(){
  local prev="$OUT/previous-run" items p found=()
  shopt -s nullglob
  items=("$OUT"/0-run-status.* "$OUT"/1-dns.txt "$OUT"/1-httpx.json "$OUT"/1-rdap-*.json "$OUT"/1-whois.txt
         "$OUT"/2-tls-ssllabs.* "$OUT"/3-headers.txt "$OUT"/3-nuclei.txt "$OUT"/3-whatweb.txt
         "$OUT"/4-urlscan* "$OUT"/4-local-capture.* "$OUT"/urlscan.png "$OUT"/dom.html "$OUT"/shot-*.png
         "$OUT"/5-seo.* "$OUT"/6-qa.* "$OUT"/qa-*.png "$OUT"/7-qa-site.* "$OUT"/qa-pages
         "$OUT"/8-a11y.* "$OUT"/9-perf.* "$OUT"/crawl "$OUT"/deep "$OUT"/link-cache.json)
  shopt -u nullglob
  for p in "${items[@]}"; do [ -e "$p" ] && found+=("$p"); done   # literal names survive nullglob
  [ ${#found[@]} -eq 0 ] && return 0
  rm -rf -- "$prev" && mkdir -p "$prev" && mv -- "${found[@]}" "$prev/" \
    && echo "== Previous run's artifacts moved to $prev/ =="
}
rotate_previous_run
# Request identity and pacing, shared with every Python collector via netutil.py. Part of the
# audit design, not tuning: the default UA identifies the audit so the site owner can separate it
# from real traffic (set AUDIT_UA to a browser string for a WAF that treats bots differently), and
# DELAY is the minimum gap in seconds between requests to the same host across all HTTP stages.
export AUDIT_UA="${AUDIT_UA:-Mozilla/5.0 (compatible; WebsiteAudit/1.0; +https://github.com/streetshadows/website-audit-tool)}"
export DELAY="${DELAY:-0.4}"
export LINK_CACHE="$OUT/link-cache.json"   # one cross-stage link-check cache per run
UA="$AUDIT_UA"
echo "== Target: $TARGET  (domain: $domain) =="

have(){ command -v "$1" >/dev/null 2>&1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

# Playwright browser resolution. Each Python Playwright release expects one specific Chromium
# revision; a preinstalled browser set (/opt/pw-browsers) drifts out of step as soon as pip
# installs a newer Playwright, and every browser stage then fails to launch. Resolve by actually
# launching: user-set PLAYWRIGHT_BROWSERS_PATH > /opt/pw-browsers > Playwright's own cache >
# any Chromium under /opt/pw-browsers via PW_CHROMIUM_EXECUTABLE (the collectors pass it as
# executable_path). Preflight records which one was used, so drift is never silent.
PYBROWSERS="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
PW_DETAIL=""
pw_launch(){  # $1 = browsers path, $2 = optional explicit executable
  PLAYWRIGHT_BROWSERS_PATH="$1" python3 - "${2:-}" >/dev/null 2>&1 <<'PY'
import sys
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch(executable_path=sys.argv[1] or None).close()
PY
}
resolve_browsers(){
  local exe
  if [ -n "${PLAYWRIGHT_BROWSERS_PATH:-}" ]; then
    PYBROWSERS="$PLAYWRIGHT_BROWSERS_PATH"
    pw_launch "$PYBROWSERS" && { PW_DETAIL="PLAYWRIGHT_BROWSERS_PATH=$PYBROWSERS (user-set)"; return 0; }
  fi
  for PYBROWSERS in /opt/pw-browsers "$HOME/.cache/ms-playwright"; do
    pw_launch "$PYBROWSERS" && { PW_DETAIL="matching Chromium in $PYBROWSERS"; return 0; }
  done
  PYBROWSERS=/opt/pw-browsers
  for exe in $(ls -d /opt/pw-browsers/chromium-*/chrome-linux*/chrome 2>/dev/null | sort -rV); do
    if pw_launch "$PYBROWSERS" "$exe"; then
      export PW_CHROMIUM_EXECUTABLE="$exe"
      PW_DETAIL="version drift: Playwright's expected Chromium absent; using $exe"
      return 0
    fi
  done
  PW_DETAIL="no launchable Chromium for the installed Python Playwright"
  return 1
}

# ProjectDiscovery's `httpx` shares its name with the popular Python `httpx` library,
# whose pip console script can shadow the real binary on PATH (a common gotcha when
# semgrep, mcp, or anything depending on python-httpx is installed). Resolve the
# ProjectDiscovery binary specifically: it accepts `-version` (exit 0); the Python CLI
# errors on it (exit 2). Fall back to common Go bin locations if PATH points at the wrong one.
resolve_httpx(){
  local c p
  for c in httpx "${GOBIN:-}/httpx" "$HOME/go/bin/httpx" /root/go/bin/httpx /usr/local/go/bin/httpx; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    if "$p" -version >/dev/null 2>&1; then echo "$p"; return 0; fi
  done
  return 1
}
HTTPX="$(resolve_httpx 2>/dev/null || true)"

# ---------- secrets: load API keys from a gitignored .env (never commit keys) ----------
# Precedence: real environment > ./.env (cwd) > toolkit-dir .env. In Claude Code on
# the web, set these as environment variables/secrets in the environment config
# instead of a file. Only URLSCAN_API_KEY is used (PSI is not required — see README).
for envf in "$HERE/.env" "./.env"; do
  # shellcheck disable=SC1090  # .env path is intentionally dynamic (cwd + toolkit dir)
  if [ -f "$envf" ]; then set -a; . "$envf"; set +a; echo "loaded secrets from $envf"; fi
done

# ---------- run-status manifest (deterministic per-stage error checking) ----------
# Every stage records ok | failed | skipped(reason) so nothing fails silently and
# the report can show exactly what ran, what was degraded, and why.
STATUS_ROWS=()
rec(){ STATUS_ROWS+=("$1"$'\t'"$2"$'\t'"$3"); printf '  [status] %-16s %-8s %s\n' "$1" "$2" "$3"; }
json_esc(){ printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
write_status(){
  { printf 'STAGE\tSTATUS\tDETAIL\n'; printf '%s\n' "${STATUS_ROWS[@]}"; } > "$OUT/0-run-status.txt"
  { echo '['; local first=1 st stt det
    for r in "${STATUS_ROWS[@]}"; do
      IFS=$'\t' read -r st stt det <<<"$r"
      [ $first -eq 1 ] || echo ','; first=0
      printf '  {"stage":"%s","status":"%s","detail":"%s"}' "$(json_esc "$st")" "$(json_esc "$stt")" "$(json_esc "$det")"
    done; echo; echo ']'; } > "$OUT/0-run-status.json"
}
trap write_status EXIT   # write the manifest even if the script is interrupted

# ---------- PREFLIGHT self-test: verify every prerequisite before scanning ----------
# Checklist of all tools/libraries the pipeline uses. CORE tools abort the run if
# missing (no point scanning without them); SUPPLEMENTARY tools only warn. Results
# are printed and recorded so coverage is explicit from the first line of output.
preflight(){
  echo "== Preflight: prerequisite self-test =="
  printf '  [info]    request pacing: DELAY=%ss per host; MAX_PAGES=%s; QA_MAX_LINKS=%s\n' \
    "$DELAY" "${MAX_PAGES:-150}" "${QA_MAX_LINKS:-300}"
  printf '  [info]    user-agent (HTTP stages): %s\n' "$AUDIT_UA"
  rec "run-identity" ok "started $(date -u +%Y-%m-%dT%H:%M:%SZ); UA=$AUDIT_UA; DELAY=${DELAY}s"
  local missing_core=() missing_supp=() t
  # name:kind  (core|supp)  — checker
  local core="python3 dig curl jq nuclei whatweb shot-scraper aspell node"
  local supp="lighthouse pdftoppm convert"
  for t in $core; do
    if have "$t"; then printf '  [ok]      %s\n' "$t"; else printf '  [MISSING] %s (core)\n' "$t"; missing_core+=("$t"); fi
  done
  # httpx checked specifically: must be the ProjectDiscovery binary, not the Python lib
  # CLI of the same name (which would shadow it on PATH and break the fingerprint stage).
  if [ -n "$HTTPX" ]; then printf '  [ok]      httpx (ProjectDiscovery: %s)\n' "$HTTPX"
  elif have httpx; then printf '  [MISSING] httpx — found a non-ProjectDiscovery "httpx" (the Python library CLI) shadowing it on PATH\n'; missing_core+=("httpx")
  else printf '  [MISSING] httpx (core)\n'; missing_core+=("httpx"); fi
  if python3 -c 'import playwright' 2>/dev/null; then
    printf '  [ok]      python:playwright\n'
    if resolve_browsers; then printf '  [ok]      chromium (%s)\n' "$PW_DETAIL"; rec "preflight-browser" ok "$PW_DETAIL"
    else printf '  [MISSING] chromium — %s (core)\n' "$PW_DETAIL"; rec "preflight-browser" failed "$PW_DETAIL"; missing_core+=("chromium"); fi
  else printf '  [MISSING] python:playwright (core)\n'; missing_core+=("playwright"); fi
  python3 -c 'import PIL' 2>/dev/null && printf '  [ok]      python:Pillow\n' || { printf '  [MISSING] python:Pillow (core)\n'; missing_core+=("Pillow"); }
  for t in $supp; do
    if have "$t"; then printf '  [ok]      %s\n' "$t"; else printf '  [warn]    %s (supplementary)\n' "$t"; missing_supp+=("$t"); fi
  done
  # secrets (presence only — never print the value)
  if [ -n "${URLSCAN_API_KEY:-}" ]; then printf '  [ok]      URLSCAN_API_KEY (detected)\n'; rec "preflight-urlscan-key" ok "URLSCAN_API_KEY detected"
  else printf '  [warn]    URLSCAN_API_KEY not set (external reputation vantage will be skipped)\n'; rec "preflight-urlscan-key" skipped "URLSCAN_API_KEY not set"; fi

  if [ ${#missing_supp[@]} -gt 0 ]; then rec "preflight-supplementary" skipped "missing: ${missing_supp[*]}"; fi
  if [ ${#missing_core[@]} -gt 0 ]; then
    rec "preflight" failed "missing CORE tools: ${missing_core[*]}"
    echo "PREFLIGHT FAILED — missing core tools: ${missing_core[*]}"
    echo "Install everything with: .claude/hooks/session-start.sh  (or see README 'Install')."
    if [ "${PREFLIGHT_STRICT:-1}" = "1" ]; then echo "Aborting (set PREFLIGHT_STRICT=0 to scan anyway)."; exit 3; fi
  else
    rec "preflight" ok "all core prerequisites present"
  fi
}
preflight

# ---------- 0. Discovery / crawl (robots, sitemap, llms/ai, well-known, page list) ----------
# Builds the page list every multi-page stage iterates over, and reports site
# hygiene files. Scales via MAX_PAGES / MAX_DEPTH / DELAY (export to tune).
if have python3; then
  if python3 "$HERE/crawl.py" "$TARGET" "$OUT"; then
    pages=$(jq -r '.page_count // "?"' "$OUT/crawl/discovery.json" 2>/dev/null)
    rec "0-crawl" ok "discovery complete (${pages} pages)"
  else rec "0-crawl" failed "crawl.py returned non-zero"; fi
else rec "0-crawl" skipped "python3 not installed"; fi

# ---------- 1. DNS / infrastructure ----------
if have dig; then
  # DNSSEC is determined authoritatively by whether the zone is SIGNED (publishes a
  # DNSKEY) and ANCHORED at the parent (a DS record) — not by the AD "authenticated
  # data" flag, which is resolver-dependent (a non-validating resolver never sets it,
  # and it can also match spuriously), giving false positives/negatives.
  # `dig +short` prints nothing both for "no such record" and for "no resolver reachable", so
  # probe the resolver first (SOA, full output for the rcode) and check each DNSSEC lookup's exit
  # code: an unreachable resolver must be a failed stage, never "DNSSEC:off".
  soa_probe="$(dig +time=3 +tries=2 SOA "$domain" 2>&1)"; dig_rc=$?
  dns_rcode="$(printf '%s\n' "$soa_probe" | sed -nE 's/.*status: ([A-Z]+).*/\1/p' | head -1)"
  dnskey="$(dig +time=3 +tries=2 +short DNSKEY "$domain" 2>/dev/null)"; dnskey_rc=$?
  ds="$(dig +time=3 +tries=2 +short DS "$domain" 2>/dev/null)"; ds_rc=$?
  {
    echo "### A";     dig +short A "$domain"
    echo "### AAAA";  dig +short AAAA "$domain"
    echo "### NS";    dig +short NS "$domain"
    echo "### MX";    dig +short MX "$domain"
    echo "### TXT";   dig +short TXT "$domain"
    echo "### SPF";   dig +short TXT "$domain" | grep -i spf
    echo "### DMARC"; dig +short TXT "_dmarc.$domain"
    echo "### CAA";   dig +short CAA "$domain"
    echo "### SOA";   dig +short SOA "$domain"
    echo "### DNSSEC"; echo "DNSKEY:"; printf '%s\n' "$dnskey"; echo "DS (parent):"; printf '%s\n' "$ds"
  } | tee "$OUT/1-dns.txt"
  if [ "$dig_rc" -ne 0 ] || [ -z "$dns_rcode" ]; then
    rec "1-dns" failed "resolver unreachable (dig exit $dig_rc) — no DNS data; DNSSEC not determined"
  elif [ "$dns_rcode" != "NOERROR" ]; then
    rec "1-dns" failed "resolver answered $dns_rcode for $domain — DNS records and DNSSEC not determined"
  elif [ "$dnskey_rc" -ne 0 ] || [ "$ds_rc" -ne 0 ]; then
    rec "1-dns" failed "dig OK but DNSKEY/DS lookup failed (exit $dnskey_rc/$ds_rc) — DNSSEC not determined"
  else
    if   [ -z "$dnskey" ]; then dnssec="DNSSEC:off (unsigned)"
    elif [ -z "$ds" ];     then dnssec="DNSSEC:partial (zone signed; no DS at parent — not anchored)"
    else                        dnssec="DNSSEC:on"; fi
    rec "1-dns" ok "dig OK; ${dnssec}"
  fi
else rec "1-dns" skipped "dig (dnsutils) not installed"; fi

# 1b. Registration data via RDAP over HTTPS (port 443) — the IETF replacement for
# port-43 whois (RFC 7480-7484). Works where whois/43 is firewalled. rdap.org is the
# IANA-backed bootstrap that redirects to the authoritative registry/RIR. We query
# both the DOMAIN (registrar, dates, status, nameservers) and the IP (network/org).
# dig +short prints resolver errors (";; communications error ...") on stdout: keep only an IPv4.
IP=$(dig +time=3 +tries=2 +short A "$domain" 2>/dev/null | grep -E '^[0-9]+(\.[0-9]+){3}$' | head -1)
rdap_dom="$OUT/1-rdap-domain.json"; rdap_ip="$OUT/1-rdap-ip.json"
curl -sS -L --max-time 30 -A "$UA" -H 'Accept: application/rdap+json' "https://rdap.org/domain/$domain" -o "$rdap_dom" || true
[ -n "$IP" ] && curl -sS -L --max-time 30 -A "$UA" -H 'Accept: application/rdap+json' "https://rdap.org/ip/$IP" -o "$rdap_ip" || true
{
  echo "### RDAP domain $domain (via HTTPS/443)"
  if have jq && jq -e .objectClassName "$rdap_dom" >/dev/null 2>&1; then
    jq -r '"handle: \(.handle // .ldhName // "?")",
           "status: \((.status // []) | join(", "))",
           "nameservers: \(([.nameservers[]?.ldhName] | join(", ")) // "?")",
           (.events[]? | "event: \(.eventAction) = \(.eventDate)"),
           (.entities[]? | "entity: \((.roles // []) | join("/")) = \(.vcardArray[1]? | map(select(.[0]=="fn"))[0][3] // .handle // "?")")' "$rdap_dom"
  else echo "(domain RDAP unavailable)"; fi
  echo "### RDAP IP $IP"
  if have jq && jq -e .objectClassName "$rdap_ip" >/dev/null 2>&1; then
    jq -r '"network: \(.name // "?") (\(.handle // "?"))",
           "range: \(.startAddress // "?") - \(.endAddress // "?")  CIDR \((.cidr0_cidrs[]? | "\(.v4prefix // .v6prefix)/\(.length)") // "?")",
           "country: \(.country // "?")",
           (.entities[]? | "org: \((.roles // []) | join("/")) = \(.vcardArray[1]? | map(select(.[0]=="fn"))[0][3] // .handle // "?")")' "$rdap_ip"
  else echo "(IP RDAP unavailable)"; fi
} | tee "$OUT/1-whois.txt"
# Domain and IP registration are recorded separately: one succeeding must not mask the other.
if have jq && jq -e .objectClassName "$rdap_dom" >/dev/null 2>&1; then
  rec "1-rdap-domain" ok "domain registration via RDAP/HTTPS"
else rec "1-rdap-domain" failed "domain RDAP returned no data (registrar/dates/status unknown)"; fi
if [ -z "$IP" ]; then
  rec "1-rdap-ip" skipped "no A record resolved — IP registration not looked up"
elif have jq && jq -e .objectClassName "$rdap_ip" >/dev/null 2>&1; then
  rec "1-rdap-ip" ok "IP network registration via RDAP/HTTPS"
# fallback to classic whois only if IP RDAP gave nothing and port 43 happens to be open
elif have whois && timeout 20 whois "$IP" 2>&1 | grep -iE "netname|org|country|inetnum|netrange|origin" >> "$OUT/1-whois.txt"; then
  rec "1-rdap-ip" ok "IP RDAP empty; classic whois/43 fallback succeeded"
else rec "1-rdap-ip" failed "IP RDAP returned no data and whois/43 unavailable"; fi
if [ -n "$HTTPX" ]; then
  if "$HTTPX" -u "$TARGET" -title -status-code -server -tech-detect -tls-grab -json -o "$OUT/1-httpx.json" -silent \
     && [ -s "$OUT/1-httpx.json" ]; then
    rec "1-httpx" ok "fingerprint OK (NB: -tls-grab sees the egress-proxy cert, not the site's)"
  else rec "1-httpx" failed "httpx error or empty output"; fi
else rec "1-httpx" skipped "ProjectDiscovery httpx not found (a Python 'httpx' may be shadowing it on PATH)"; fi

# ---------- 2. TLS ----------
# IMPORTANT: local handshake tools (openssl/httpx -tls-grab) are UNRELIABLE behind a
# TLS-intercepting egress proxy — they observe the proxy's cert, not the site's, so a
# local scan is structurally incapable of measuring the site's real TLS here.
# Authoritative TLS therefore comes from scanners that handshake from THEIR OWN
# servers: Qualys SSL Labs (grade), cross-confirmed by urlscan.io's external render
# (§5.4), which also re-observes the real site certificate from outside the proxy.

# Qualys SSL Labs — real handshake, grade + protocols + cert (can take 1-3 min)
echo "== TLS: Qualys SSL Labs (handshake from their servers; may take a few minutes) =="
curl -sS "https://api.ssllabs.com/api/v3/analyze?host=$domain&startNew=on&all=done" >/dev/null 2>&1 || true
for i in $(seq 1 40); do
  sl=$(curl -sS "https://api.ssllabs.com/api/v3/analyze?host=$domain&all=done" 2>/dev/null)
  st=$(echo "$sl" | jq -r '.status // empty' 2>/dev/null)
  echo "  ssllabs status: ${st:-?} ($i)"
  if [ "$st" = "READY" ] || [ "$st" = "ERROR" ]; then echo "$sl" > "$OUT/2-tls-ssllabs.json"; break; fi
  sleep 15
done
if have jq && [ -s "$OUT/2-tls-ssllabs.json" ]; then
  {
    jq -r '.endpoints[]? | "endpoint \(.ipAddress)  grade \(.grade)  hasWarnings \(.hasWarnings)"' "$OUT/2-tls-ssllabs.json"
    jq -r '.endpoints[0].details | "protocols: " + ([.protocols[]?|"\(.name) \(.version)"] | join(", "))' "$OUT/2-tls-ssllabs.json"
    jq -r '.certs[0]? | "cert: \(.subject) | issuer \(.issuerSubject) | \(.keyAlg) \(.keySize) \(.sigAlg)"' "$OUT/2-tls-ssllabs.json"
  } 2>/dev/null | tee "$OUT/2-tls-ssllabs.txt"
fi
sl_st=$(jq -r '.status // "no-response"' "$OUT/2-tls-ssllabs.json" 2>/dev/null); sl_st="${sl_st:-no-response}"
if [ "$sl_st" = "READY" ]; then
  rec "2-tls-ssllabs" ok "grade $(jq -r '[.endpoints[]?.grade]|unique|join("/")' "$OUT/2-tls-ssllabs.json" 2>/dev/null)"
else rec "2-tls-ssllabs" failed "did not complete (status=$sl_st)"; fi

# ---------- 3. Headers + misconfig + tech ----------
{
  echo "=== HTTPS headers ==="; curl -sIL -A "$UA" "$TARGET"
  echo "=== HTTP (redirect check) ==="; curl -sIL -A "$UA" "http://$domain"
} | tee "$OUT/3-headers.txt"
if grep -qiE "^HTTP/" "$OUT/3-headers.txt"; then rec "3-headers" ok "response headers captured"; else rec "3-headers" failed "no HTTP response (egress blocked?)"; fi
if have nuclei; then
  # env -u GITHUB_TOKEN: nuclei's startup template/version check hits the GitHub API; an
  # unrelated GITHUB_TOKEN in the environment makes it 401 and abort. Templates are provisioned
  # by the session-start hook (see .claude/hooks/session-start.sh).
  if env -u GITHUB_TOKEN -u GH_TOKEN \
       nuclei -u "$TARGET" -tags misconfig,headers,tech -severity info,low,medium,high -o "$OUT/3-nuclei.txt" -silent; then
    rec "3-nuclei" ok "$(wc -l < "$OUT/3-nuclei.txt" | tr -d ' ') finding(s)"
  else rec "3-nuclei" failed "nuclei returned non-zero (templates missing? run: nuclei -update-templates)"; fi
else rec "3-nuclei" skipped "nuclei not installed"; fi
if have whatweb; then
  RUBYLIB="${RUBYLIB:-/usr/lib/ruby/vendor_ruby}" whatweb "$TARGET" | tee "$OUT/3-whatweb.txt" && rec "3-whatweb" ok "tech fingerprint captured"
else rec "3-whatweb" skipped "whatweb not installed (tech stack inferred from httpx + DOM)"; fi

# ---------- 4. urlscan.io (needs API key) + local browser capture fallback ----------
# The documented urlscan API requires an API key. Anonymous scans only work via
# the urlscan.io website, which is reCAPTCHA-gated and not automatable. So: use
# urlscan if URLSCAN_API_KEY is set; otherwise fall back to a LOCAL Playwright
# capture (DOM + network requests + cookies + screenshot), which reproduces most
# of urlscan's data — minus urlscan's independent, non-proxied external vantage.
if [ -n "${URLSCAN_API_KEY:-}" ]; then
  HDR=(-H "Content-Type: application/json" -H "API-Key: $URLSCAN_API_KEY")
  uuid=$(curl -sS "${HDR[@]}" -X POST https://urlscan.io/api/v1/scan/ \
    -d "{\"url\":\"$TARGET\",\"visibility\":\"unlisted\"}" | jq -r '.uuid // empty')
  if [ -n "$uuid" ]; then
    # NB result/screenshot/dom endpoints all need the API-Key header for unlisted scans.
    echo "urlscan uuid=$uuid"; for i in $(seq 1 24); do
      code=$(curl -sS -H "API-Key: $URLSCAN_API_KEY" -o "$OUT/4-urlscan.json" -w '%{http_code}' "https://urlscan.io/api/v1/result/$uuid/")
      [ "$code" = "200" ] && break; sleep 10; done
    curl -sS -H "API-Key: $URLSCAN_API_KEY" -o "$OUT/urlscan.png" "https://urlscan.io/screenshots/$uuid.png"
    curl -sS -H "API-Key: $URLSCAN_API_KEY" -o "$OUT/dom.html"    "https://urlscan.io/dom/$uuid/"
    jq '{threat_verdict:.verdicts.overall, ip:.page.ip, asn:.page.asn, country:.page.country, server:.page.server, tlsIssuer:.page.tlsIssuer, tech:[.meta.processors.wappa.data[]?.app], num_requests:(.data.requests|length), contacted_domains:.lists.domains, contacted_ips:.lists.ips, certificates:.lists.certificates, cookies:.data.cookies}' "$OUT/4-urlscan.json" 2>/dev/null | tee "$OUT/4-urlscan-summary.txt"
    rec "4-urlscan" ok "external scan $uuid"
  else echo "urlscan submission failed (check key / egress)" | tee "$OUT/4-urlscan.json"; rec "4-urlscan" failed "submission failed (key/egress)"; fi
else
  echo "No URLSCAN_API_KEY set — urlscan API requires a key (anonymous scans only via the reCAPTCHA-gated website). Using local browser capture instead." | tee "$OUT/4-urlscan.json"
  rec "4-urlscan" skipped "no URLSCAN_API_KEY — independent external reputation/render NOT obtained; local capture used instead"
fi

# Local browser capture (DOM + network map + cookies). Runs as the no-key
# fallback above, and is always useful as an independent local record.
if [ ! -s "$OUT/dom.html" ] && have python3 && python3 -c 'import playwright' 2>/dev/null; then
  if PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" \
       python3 "$HERE/capture-local.py" "$TARGET" "$OUT" | tee "$OUT/4-local-capture.txt"; then
    rec "4-local-capture" ok "local DOM/network/cookies captured"
  else echo "local capture failed" | tee -a "$OUT/4-local-capture.txt"; rec "4-local-capture" failed "capture-local.py error"; fi
fi

# ---------- 5. Responsive screenshots (for UI / CSS review) ----------
# Notes:
#  - --ignore-certificate-errors: required behind a TLS-intercepting egress proxy
#    (its CA is not trusted by the bundled browser).
#  - --reduced-motion + force-reveal JS: many sites hide content with opacity:0
#    and reveal it on scroll via IntersectionObserver. A full-page capture does
#    NOT scroll each section into view, so those sections render BLANK unless we
#    force them visible. --reduced-motion triggers prefers-reduced-motion
#    fallbacks; the JS handles common reveal patterns (.reveal/.in-view, AOS).
# Widths: 390 mobile, 768 tablet, 1280 laptop, 1440 desktop.
REVEAL_JS="document.querySelectorAll('[class*=reveal],[data-aos]').forEach(function(e){e.classList.add('in-view','aos-animate','visible');e.style.opacity=1;e.style.transform='none';});"
if have shot-scraper; then
  ok5=0
  for w in 390 768 1280 1440; do
    # env -u *_PROXY: shot-scraper's Chromium mis-handles the TLS-intercepting egress proxy and
    # RSTs HTTPS. Open outbound is required (see header); --ignore-certificate-errors covers the
    # transparent interception. The Python collectors clear the same vars internally.
    # PLAYWRIGHT_BROWSERS_PATH: use the browser set preflight resolved, not Playwright's default cache.
    env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" \
      shot-scraper "$TARGET" -w "$w" -o "$OUT/shot-$w.png" \
      --browser-arg --ignore-certificate-errors --reduced-motion -j "$REVEAL_JS" --wait 1800 \
      && { echo "shot-$w.png ok"; ok5=$((ok5+1)); }
  done
  if [ "$ok5" -eq 4 ]; then rec "5-screenshots" ok "4/4 viewports"; else rec "5-screenshots" failed "$ok5/4 viewports captured"; fi
else echo "shot-scraper not installed — skipping screenshots"; rec "5-screenshots" skipped "shot-scraper not installed"; fi

# ---------- 6. Website QA: UI / images / spelling / responsive (deep, per page) ----------
# Deep template/asset review. Runs on the target (-> $OUT, used by the report) and on the next
# QA_DEEP_PAGES-1 pages of crawl/urls.txt into $OUT/deep/<slug>/. urls.txt lists the target first,
# then SEED_URLS, then the rest sorted, so requested pages are reviewed first; every seed is
# reviewed even if QA_DEEP_PAGES is smaller. Each deep page gets its own run-status row.
if have python3 && python3 -c 'import playwright' 2>/dev/null; then
  have aspell || echo "(aspell not installed — spelling check will be skipped: apt install aspell aspell-en)"
  if PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" python3 "$HERE/qa-check.py" "$TARGET" "$OUT"; then
    rec "6-qa" ok "deep UI/image/spelling QA: $TARGET"
  else rec "6-qa" failed "qa-check.py error on $TARGET"; fi
  n_seeds=$(jq -r '.seed_urls | length' "$OUT/crawl/discovery.json" 2>/dev/null || echo 0)
  QA_DEEP_PAGES="${QA_DEEP_PAGES:-3}"
  [ "$QA_DEEP_PAGES" -lt $((n_seeds+1)) ] && QA_DEEP_PAGES=$((n_seeds+1))
  if [ -f "$OUT/crawl/urls.txt" ] && [ "$QA_DEEP_PAGES" -gt 1 ]; then
    # mapfile, not `| while`: a pipe would run the loop in a subshell and lose its rec() rows.
    mapfile -t deep_urls < <(tail -n +2 "$OUT/crawl/urls.txt" | head -n $((QA_DEEP_PAGES-1)))
    n=0
    for purl in "${deep_urls[@]}"; do
      [ -z "$purl" ] && continue
      n=$((n+1))
      slug=$(echo "$purl" | sed -E 's#^https?://##; s#[^a-zA-Z0-9]+#_#g' | cut -c1-50)
      mkdir -p "$OUT/deep/$slug"
      if PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" python3 "$HERE/qa-check.py" "$purl" "$OUT/deep/$slug" >/dev/null 2>&1
      then rec "6-qa-deep-$n" ok "$purl -> deep/$slug/"
      else rec "6-qa-deep-$n" failed "qa-check.py error on $purl"; fi
    done
  fi
else echo "python3/playwright missing — skipping QA section"; rec "6-qa" skipped "python3/playwright not available"; fi

# ---------- 7. SEO / metadata / links across ALL discovered pages ----------
if have python3; then
  if python3 "$HERE/seo-check.py" "$OUT"; then
    rec "7-seo" ok "$(jq -r '.pages_checked // "?"' "$OUT/5-seo.json" 2>/dev/null) pages checked"
  else rec "7-seo" failed "seo-check.py error"; fi
else rec "7-seo" skipped "python3 not installed"; fi

# ---------- 8. Multi-page responsive regression across ALL pages ----------
if have python3 && python3 -c 'import playwright' 2>/dev/null; then
  if PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" python3 "$HERE/qa-site.py" "$OUT"; then
    rec "8-qa-site" ok "multi-page responsive regression"
  else rec "8-qa-site" failed "qa-site.py error"; fi
else rec "8-qa-site" skipped "python3/playwright not available"; fi

# ---------- 9. Accessibility (axe-core + independent contrast cross-check) ----------
if have python3 && python3 -c 'import playwright' 2>/dev/null; then
  PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" python3 "$HERE/a11y-check.py" "$OUT"; rc=$?
  a11y_counts="$(jq -r '"axe ran on \(.axe_pages_scanned // "?")/\(.pages_checked // "?") pages; \(.contrast_summary.genuine_failures // "?") genuine contrast fails; \(.contrast_summary.indeterminate_gradient_or_image // 0) indeterminate"' "$OUT/8-a11y.json" 2>/dev/null)"
  if   [ "$rc" -eq 0 ]; then rec "9-a11y" ok "$a11y_counts"
  elif [ "$rc" -eq 3 ]; then rec "9-a11y" failed "axe-core did not run on any page (contrast cross-check only): $a11y_counts"
  else rec "9-a11y" failed "a11y-check.py error"; fi
else rec "9-a11y" skipped "python3/playwright not available"; fi

# ---------- 10. Performance / Lighthouse (local Lighthouse — standard method) ----------
# Local Lighthouse is the standard performance method for this toolkit. Its timing
# (LCP/FCP/SI) is indicative only from a datacenter, but the category scores and the
# structural audits (CLS, unsized images, etc.) are trustworthy — which is the value
# we want. PSI is not used (it required a key for little added benefit; see README).
if have python3; then
  # perf-check.py resolves Chromium via Playwright (honouring CHROME_PATH if set) and exits
  # non-zero when Lighthouse produced no scores, so the recorded status reflects real data
  # rather than a non-empty 9-perf.json that only contains an error object.
  if python3 "$HERE/perf-check.py" "$OUT"; then
    rec "10-perf" ok "local Lighthouse (scores + structural audits reliable; timing indicative)"
  else rec "10-perf" failed "no Lighthouse scores (check lighthouse install / CHROME_PATH)"; fi
else rec "10-perf" skipped "python3 not installed"; fi

rec "run-end" ok "finished $(date -u +%Y-%m-%dT%H:%M:%SZ)"
write_status; trap - EXIT
echo "== Done. Artifacts in $OUT/ =="
echo "Run status: $OUT/0-run-status.txt  (per-stage ok/failed/skipped)"
echo "Stages: 0 crawl | 1 DNS | 2 TLS | 3 headers/tech | 4 urlscan | 5 screenshots | 6 deep QA | 7 SEO | 8 responsive | 9 a11y | 10 perf"
