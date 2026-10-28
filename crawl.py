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
"""

import gzip
import json
import os
import re
import sys
import time
import urllib.request
from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

UA = "Mozilla/5.0 (compatible; SiteAudit/1.0; +audit)"
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
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or url.endswith(".gz"):
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
        return r.status, data.decode("utf-8", "replace")


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


def link_bfs(base, base_host, cap, max_depth, delay, include_sub):
    seen, pages, q = set(), set(), deque([(base, 0)])
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
        time.sleep(delay)
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
    delay = float(os.environ.get("DELAY", 0.4))
    mode = os.environ.get("MODE", "auto")
    include_sub = os.environ.get("INCLUDE_SUBDOMAINS", "0") == "1"

    disc = {"base": base, "host": base_host, "config": {"max_pages": cap, "max_depth": max_depth, "mode": mode}}

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

    # well-known hygiene files
    scheme = urlparse(base).scheme
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
        wk[f] = status_only(f"{scheme}://{base_host}/{f}")
    disc["well_known"] = wk

    # discover URLs
    urls = set([base])
    sm_candidates = list(
        dict.fromkeys(sitemaps + [f"{scheme}://{base_host}/sitemap.xml", f"{scheme}://{base_host}/sitemap_index.xml"])
    )
    used = "none"
    if mode in ("auto", "sitemap"):
        seen = set()
        for sm in sm_candidates:
            if status_only(sm) and status_only(sm) < 400:
                collect_sitemap(sm, seen, urls, cap)
        if len(urls) > 1:
            used = "sitemap"
    if used == "none" and mode in ("auto", "links"):
        urls |= link_bfs(base, base_host, cap, max_depth, delay, include_sub)
        used = "link-crawl"
    urls = sorted(u for u in urls if same_site(urlparse(u).netloc, base_host, include_sub))[:cap]

    disc["discovery_method"] = used
    disc["page_count"] = len(urls)
    disc["truncated_at_cap"] = len(urls) >= cap
    open(os.path.join(out, "urls.txt"), "w").write("\n".join(urls) + "\n")
    json.dump(disc, open(os.path.join(out, "discovery.json"), "w"), indent=2)

    print(f"discovery: method={used} pages={len(urls)} (cap {cap})")
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
