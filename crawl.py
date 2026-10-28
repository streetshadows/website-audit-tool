#!/usr/bin/env python3
"""Site discovery / crawl — stage 0 of the audit pipeline.

Builds the page list every later stage iterates over, and reports site-level
"hygiene" files. Designed to scale: it prefers the sitemap, falls back to a
breadth-first link crawl, and is bounded by MAX_PAGES / MAX_DEPTH / DELAY so it
stays reliable on large sites (slow is fine; runaway is not).

Checks/handles:
  * robots.txt        — presence, Sitemap: directives, and AI-crawler policy
                        (GPTBot, ClaudeBot, Google-Extended, CCBot, PerplexityBot, ...)
  * sitemaps          — recursive: sitemap index -> child sitemaps -> URLs (+ .gz)
  * well-known files  — llms.txt, llms-full.txt, ai.txt, security.txt, humans.txt, favicon
  * link BFS fallback — same-site <a href> discovery when no usable sitemap
Outputs: <out>/crawl/urls.txt, <out>/crawl/discovery.json, <out>/crawl/robots.txt

Usage: crawl.py <base_url> <out_dir>
Env: MAX_PAGES(150) MAX_DEPTH(4) DELAY(0.4) MODE(auto|sitemap|links) INCLUDE_SUBDOMAINS(0)
     SEED_URLS — extra pages to include, comma/space separated, absolute or site-relative.
                 Needed for SPAs, whose client-side routes never appear as links in raw HTML.
urls.txt order: the target first, then SEED_URLS, then everything else sorted — later stages
(and the MAX_PAGES cap) rely on that, so the requested pages are never displaced.
"""

import gzip
import json
import os
import re
import secrets
import sys
import urllib.request
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netutil  # shared UA + per-host pacing (sibling module)

UA = netutil.AUDIT_UA
AI_BOTS = [
    "GPTBot",
    "ClaudeBot",
    "Claude-Web",
    "anthropic-ai",
    "CCBot",
    "Google-Extended",
    "PerplexityBot",
    "Bytespider",
    "Amazonbot",
    "Applebot-Extended",
    "Meta-ExternalAgent",
    "cohere-ai",
]
SKIP_EXT = re.compile(
    r"\.(jpg|jpeg|png|gif|svg|webp|ico|pdf|zip|gz|mp4|mp3|"
    r"woff2?|ttf|eot|css|js|json|xml|rss|doc|docx|xls|xlsx)$",
    re.I,
)


def get(url, timeout=20):
    netutil.PACER.wait(url)  # every crawl request (robots, well-known, sitemaps, BFS) is paced
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
        return r.status, data.decode("utf-8", "replace")


def fetch_or_none(url, timeout=15):
    """(status, body) without raising: HTTP errors give (code, ""), network errors (None, "")."""
    try:
        return get(url, timeout)
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return None, ""


def status_only(url, timeout=15):
    try:
        return get(url, timeout)[0]
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def norm(url):
    url, _ = urldefrag(url)
    p = urlparse(url)
    if not p.scheme.startswith("http"):
        return None
    path = re.sub(r"/+$", "", p.path) or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}" + (f"?{p.query}" if p.query else "")


def same_site(host, base_host, include_sub):
    host, base_host = host.lower(), base_host.lower()
    if host == base_host:
        return True
    if include_sub:
        reg = ".".join(base_host.split(".")[-2:])
        return host.endswith("." + reg)
    return host == base_host or host == "www." + base_host or "www." + host == base_host


def parse_robots(text):
    sitemaps, groups, cur = [], {}, ["*"]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        kl = k.lower()
        if kl == "sitemap":
            sitemaps.append(v)
        elif kl == "user-agent":
            cur = [v]
            groups.setdefault(v, [])
        elif kl in ("disallow", "allow"):
            for ua in cur:
                groups.setdefault(ua, []).append((kl, v))
    return sitemaps, groups


def ai_policy(groups):
    pol = {}
    for bot in AI_BOTS:
        rules = None
        for ua, rs in groups.items():
            if ua.lower() == bot.lower():
                rules = rs
                break
        if rules is None:
            pol[bot] = "not mentioned (allowed by default)"
        else:
            blocked = any(k == "disallow" and v.strip() in ("/", "*") for k, v in rules)
            pol[bot] = "BLOCKED" if blocked else "allowed (explicit)"
    return pol


def collect_sitemap(url, seen, urls, cap, depth=0):
    if depth > 5 or url in seen or len(urls) >= cap:
        return
    seen.add(url)
    try:
        _, body = get(url)
    except Exception:
        return
    locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body, re.I)
    is_index = "<sitemapindex" in body.lower()
    for loc in locs:
        if len(urls) >= cap:
            break
        if is_index:
            collect_sitemap(loc.strip(), seen, urls, cap, depth + 1)
        else:
            n = norm(loc.strip())
            if n:
                urls.add(n)


def link_bfs(base, base_host, cap, max_depth, include_sub, seeds=()):
    seen, pages, q = set(), set(), deque([(base, 0)] + [(s, 0) for s in seeds])
    while q and len(pages) < cap:
        url, d = q.popleft()
        if url in seen or d > max_depth:
            continue
        seen.add(url)
        try:
            st, html = get(url)
        except Exception:
            continue
        if st and st < 400:
            pages.add(url)
        for m in re.findall(r'href=["\']([^"\'#]+)', html):
            nu = norm(urljoin(url, m))
            if not nu or nu in seen:
                continue
            p = urlparse(nu)
            if not same_site(p.netloc, base_host, include_sub):
                continue
            if SKIP_EXT.search(p.path):
                continue
            if nu not in pages:
                q.append((nu, d + 1))
    return pages


def main():
    if len(sys.argv) < 3:
        print("usage: crawl.py <base_url> <out_dir>")
        return 2
    base = norm(sys.argv[1]) or sys.argv[1]
    out = os.path.join(sys.argv[2].rstrip("/"), "crawl")
    os.makedirs(out, exist_ok=True)
    base_host = urlparse(base).netloc
    cap = int(os.environ.get("MAX_PAGES", 150))
    max_depth = int(os.environ.get("MAX_DEPTH", 4))
    mode = os.environ.get("MODE", "auto")
    include_sub = os.environ.get("INCLUDE_SUBDOMAINS", "0") == "1"
    seeds, seeds_ignored = [], []
    for raw in re.split(r"[,\s]+", os.environ.get("SEED_URLS", "").strip()):
        n = norm(urljoin(base, raw)) if raw else None
        if n and same_site(urlparse(n).netloc, base_host, include_sub):
            if n != base and n not in seeds:
                seeds.append(n)
        elif raw:
            seeds_ignored.append(raw)  # off-site or not http(s): reported, never crawled

    disc = {
        "base": base,
        "host": base_host,
        "config": {"max_pages": cap, "max_depth": max_depth, "mode": mode, "delay_s": netutil.delay()},
        "seed_urls": seeds,
        "seed_urls_ignored": seeds_ignored,
    }

    # robots.txt
    robots_url = f"{urlparse(base).scheme}://{base_host}/robots.txt"
    sitemaps, groups, robots_present = [], {}, False
    try:
        st, rtext = get(robots_url)
        if st < 400 and "<html" not in rtext[:200].lower():
            robots_present = True
            open(os.path.join(out, "robots.txt"), "w").write(rtext)
            sitemaps, groups = parse_robots(rtext)
    except Exception:
        pass
    disc["robots_txt"] = {
        "present": robots_present,
        "sitemaps_declared": sitemaps,
        "ai_crawler_policy": ai_policy(groups)
        if robots_present
        else "no robots.txt — all crawlers (incl. AI) allowed by default; no policy expressed",
    }

    # Googlebot / sitewide blocking (catastrophic-SEO check)
    def sitewide_block(ua):
        for k, v in groups.items():
            if k.lower() in (ua.lower(), "*"):
                if any(rk == "disallow" and rv.strip() == "/" for rk, rv in v):
                    return True
        return False

    disc["robots_txt"]["googlebot_sitewide_blocked"] = robots_present and sitewide_block("Googlebot")
    disc["robots_txt"]["wildcard_disallows"] = (
        [v for k, v in groups.items() if k == "*" for rk, v in v if rk == "disallow"] if robots_present else []
    )

    # soft-404 probe: a host that answers 200 for a path that cannot exist (typical of SPA
    # hosting with an index.html fallback) makes every presence check and internal broken-link
    # check on it meaningless, so detect it first and qualify those results.
    scheme = urlparse(base).scheme
    probe_url = f"{scheme}://{base_host}/{secrets.token_hex(8)}-audit-404-probe"
    probe_st, probe_body = fetch_or_none(probe_url)
    soft404 = probe_st is not None and probe_st < 400
    disc["soft_404"] = {"probe_url": probe_url, "status": probe_st, "soft_404": soft404}

    def is_fallback(body):
        """True when a 'present' well-known file is really the soft-404 fallback page."""
        head = body.lstrip()[:300].lower()
        return body.strip() == probe_body.strip() or ("<html" in head or "<!doctype html" in head)

    # well-known hygiene files (on a soft-404 host, a 200 that is the HTML fallback is "soft-404")
    wk = {}
    for f in [
        "sitemap.xml",
        "sitemap_index.xml",
        "llms.txt",
        "llms-full.txt",
        "ai.txt",
        ".well-known/security.txt",
        "humans.txt",
        "favicon.ico",
    ]:
        st, body = fetch_or_none(f"{scheme}://{base_host}/{f}")
        wk[f] = "soft-404" if soft404 and st is not None and st < 400 and is_fallback(body) else st
    disc["well_known"] = wk

    # discover URLs
    urls = set([base, *seeds])
    sm_candidates = list(
        dict.fromkeys(sitemaps + [f"{scheme}://{base_host}/sitemap.xml", f"{scheme}://{base_host}/sitemap_index.xml"])
    )
    used = "none"
    if mode in ("auto", "sitemap"):
        seen = set()
        for sm in sm_candidates:
            st = status_only(sm)
            if st and st < 400:
                collect_sitemap(sm, seen, urls, cap)
        if len(urls) > 1 + len(seeds):  # seeds alone don't make the sitemap a success
            used = "sitemap"
    if used == "none" and mode in ("auto", "links"):
        urls |= link_bfs(base, base_host, cap, max_depth, include_sub, seeds)
        used = "link-crawl"
    rest = sorted(u for u in urls - {base, *seeds} if same_site(urlparse(u).netloc, base_host, include_sub))
    urls = [base, *seeds, *rest][:cap]

    disc["discovery_method"] = used
    disc["page_count"] = len(urls)
    disc["truncated_at_cap"] = len(urls) >= cap
    open(os.path.join(out, "urls.txt"), "w").write("\n".join(urls) + "\n")
    json.dump(disc, open(os.path.join(out, "discovery.json"), "w"), indent=2)

    print(f"discovery: method={used} pages={len(urls)} (cap {cap}); seeds={len(seeds)}")
    if soft404:
        print(f"SOFT 404: {probe_url} answered {probe_st} — unknown paths are not reported as missing")
    if seeds_ignored:
        print(f"SEED_URLS ignored (off-site or not http/https): {seeds_ignored}")
    print(
        f"robots.txt: {'present' if robots_present else 'MISSING'}; "
        f"sitemap.xml: {wk['sitemap.xml']}; llms.txt: {wk['llms.txt']}; security.txt: {wk['.well-known/security.txt']}"
    )
    if robots_present and isinstance(disc["robots_txt"]["ai_crawler_policy"], dict):
        blk = [b for b, p in disc["robots_txt"]["ai_crawler_policy"].items() if p == "BLOCKED"]
        print(f"AI crawlers blocked: {blk or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
