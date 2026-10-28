#!/usr/bin/env bash
# External website audit — run from any host with OPEN outbound network access.
# Usage: ./run-audit.sh [https://target]   (default: https://example.com)
# Optional: export URLSCAN_API_KEY=... (not required; anonymous scan is used if unset)
set -uo pipefail

TARGET="${1:-https://example.com}"
domain=$(echo "$TARGET" | sed -E 's#^https?://##; s#/.*##')
OUT="./audit"; mkdir -p "$OUT"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
echo "== Target: $TARGET  (domain: $domain) =="

have(){ command -v "$1" >/dev/null 2>&1; }
PYBROWSERS="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}"
HERE="$(cd "$(dirname "$0")" && pwd)"

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
  python3 -c 'import playwright' 2>/dev/null && printf '  [ok]      python:playwright\n' || { printf '  [MISSING] python:playwright (core)\n'; missing_core+=("playwright"); }
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
  dnskey="$(dig +short DNSKEY "$domain" 2>/dev/null)"
  ds="$(dig +short DS "$domain" 2>/dev/null)"
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
  if   [ -z "$dnskey" ]; then dnssec="DNSSEC:off (unsigned)"
  elif [ -z "$ds" ];     then dnssec="DNSSEC:partial (zone signed; no DS at parent — not anchored)"
  else                        dnssec="DNSSEC:on"; fi
  mx=$(grep -A1 "### MX" "$OUT/1-dns.txt" | tail -1); [ -z "$mx" ] && mx="no-MX"
  rec "1-dns" ok "dig OK; ${dnssec}"
else rec "1-dns" skipped "dig (dnsutils) not installed"; fi

# 1b. Registration data via RDAP over HTTPS (port 443) — the IETF replacement for
# port-43 whois (RFC 7480-7484). Works where whois/43 is firewalled. rdap.org is the
# IANA-backed bootstrap that redirects to the authoritative registry/RIR. We query
# both the DOMAIN (registrar, dates, status, nameservers) and the IP (network/org).
IP=$(dig +short A "$domain" 2>/dev/null | head -1)
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
# fallback to classic whois only if RDAP gave nothing and port 43 happens to be open
if ! grep -qE "handle:|network:" "$OUT/1-whois.txt"; then
  if have whois && timeout 20 whois "$IP" 2>&1 | grep -iE "netname|org|country|inetnum|netrange|origin" >> "$OUT/1-whois.txt"; then
    rec "1-rdap" ok "RDAP empty; classic whois/43 fallback succeeded"
  else rec "1-rdap" failed "RDAP returned no data and whois/43 unavailable"; fi
else
  rec "1-rdap" ok "registration data via RDAP/HTTPS (domain + IP)"
fi
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
sl_st=$(jq -r '.status // "no-response"' "$OUT/2-tls-ssllabs.json" 2>/dev/null)
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
  if PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/opt/pw-browsers}" \
       python3 "$(dirname "$0")/capture-local.py" "$TARGET" "$OUT" | tee "$OUT/4-local-capture.txt"; then
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
    env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy \
      shot-scraper "$TARGET" -w "$w" -o "$OUT/shot-$w.png" \
      --browser-arg --ignore-certificate-errors --reduced-motion -j "$REVEAL_JS" --wait 1800 \
      && { echo "shot-$w.png ok"; ok5=$((ok5+1)); }
  done
  if [ "$ok5" -eq 4 ]; then rec "5-screenshots" ok "4/4 viewports"; else rec "5-screenshots" failed "$ok5/4 viewports captured"; fi
else echo "shot-scraper not installed — skipping screenshots"; rec "5-screenshots" skipped "shot-scraper not installed"; fi

# ---------- 6. Website QA: UI / images / spelling / responsive (deep, per page) ----------
# Deep template/asset review. Runs on the homepage (-> $OUT, used by the report) and,
# if QA_DEEP_PAGES>1, on the next pages from the crawl into $OUT/deep/<slug>/.
if have python3 && python3 -c 'import playwright' 2>/dev/null; then
  have aspell || echo "(aspell not installed — spelling check will be skipped: apt install aspell aspell-en)"
  if PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" python3 "$HERE/qa-check.py" "$TARGET" "$OUT"; then
    rec "6-qa" ok "deep UI/image/spelling QA (homepage)"
  else rec "6-qa" failed "qa-check.py error"; fi
  QA_DEEP_PAGES="${QA_DEEP_PAGES:-3}"
  if [ -f "$OUT/crawl/urls.txt" ] && [ "$QA_DEEP_PAGES" -gt 1 ]; then
    tail -n +2 "$OUT/crawl/urls.txt" | head -n $((QA_DEEP_PAGES-1)) | while read -r purl; do
      [ -z "$purl" ] && continue
      slug=$(echo "$purl" | sed -E 's#^https?://##; s#[^a-zA-Z0-9]+#_#g' | cut -c1-50)
      mkdir -p "$OUT/deep/$slug"
      PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" \
        python3 "$HERE/qa-check.py" "$purl" "$OUT/deep/$slug" >/dev/null 2>&1 \
        && echo "deep QA: $purl -> deep/$slug/" || echo "deep QA failed: $purl"
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
  if PLAYWRIGHT_BROWSERS_PATH="$PYBROWSERS" python3 "$HERE/a11y-check.py" "$OUT"; then
    rec "9-a11y" ok "$(jq -r '.contrast_summary.genuine_failures // "?"' "$OUT/8-a11y.json" 2>/dev/null) genuine contrast fails; $(jq -r '.contrast_summary.indeterminate_gradient_or_image // 0' "$OUT/8-a11y.json" 2>/dev/null) indeterminate"
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

write_status; trap - EXIT
echo "== Done. Artifacts in $OUT/ =="
echo "Run status: $OUT/0-run-status.txt  (per-stage ok/failed/skipped)"
echo "Stages: 0 crawl | 1 DNS | 2 TLS | 3 headers/tech | 4 urlscan | 5 screenshots | 6 deep QA | 7 SEO | 8 responsive | 9 a11y | 10 perf"
