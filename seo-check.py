#!/usr/bin/env python3
"""SEO / metadata / link audit across ALL discovered pages — stage 2.

Reads <out>/crawl/urls.txt and, for every page, extracts SEO-critical signals from
the server-rendered HTML, then reports per-page issues, cross-page duplicates, and
site-wide broken links. Raw-HTML fetch (fast, reliable) — note that fully
client-rendered SPAs may need the JS-rendered DOM for body content (head tags are
almost always server-rendered).

Per page: status, <title>, meta description, canonical, meta-robots (noindex),
H1 count, html lang, viewport meta, Open Graph, Twitter card, JSON-LD type,
favicon, word count, images-missing-alt.
Cross-page: duplicate titles / descriptions, missing tags.
Site-wide: broken internal + external links (checked once, deduped).

Usage: seo-check.py <out_dir>
Env: SEO_MAX_PAGES(300) CHECK_LINKS(1)
"""

import json
import os
import re
import sys
import urllib.request
from collections import defaultdict
from urllib.parse import urldefrag, urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import standards  # standards & best-practice knowledge base (sibling module)

UA = "Mozilla/5.0 (compatible; SiteAudit/1.0)"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, dict(r.headers), r.read().decode("utf-8", "replace")


def status(url, timeout=15):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def norm_url(u):
    u, _ = urldefrag(u)
    p = urlparse(u)
    path = re.sub(r"/+$", "", p.path) or "/"
    return f"{p.scheme}://{p.netloc.lower()}{path}"


def robots_disallows(out):
    """Disallow paths that apply to Googlebot (its own group + the * group)."""
    p = os.path.join(out, "crawl", "robots.txt")
    paths = []
    if os.path.exists(p):
        cur = ["*"]
        for line in open(p):
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = [x.strip() for x in line.split(":", 1)]
            if k.lower() == "user-agent":
                cur = [v.lower()]
            elif k.lower() == "disallow" and any(c in ("*", "googlebot") for c in cur) and v:
                paths.append(v)
    return paths


def indexability(u, st, hdrs, page, html, dis):
    xr = " ".join(v for k, v in hdrs.items() if k.lower() == "x-robots-tag").lower()
    noindex_meta = page.get("noindex")
    noindex_hdr = "noindex" in xr
    path = urlparse(u).path or "/"
    blocked = any(path.startswith(d) for d in dis)
    canon = page.get("canonical")
    canon_self = canon is None or norm_url(urljoin(u, canon)) == norm_url(u)
    mixed = re.findall(r'(?:src|href)=["\'](http://[^"\']+)', html, re.I) if u.startswith("https") else []
    indexable = (st == 200) and not noindex_meta and not noindex_hdr and not blocked
    reasons = []
    if st != 200:
        reasons.append(f"HTTP {st}")
    if noindex_meta:
        reasons.append("noindex (meta robots)")
    if noindex_hdr:
        reasons.append("noindex (X-Robots-Tag header)")
    if blocked:
        reasons.append("blocked by robots.txt Disallow")
    if not canon_self:
        reasons.append(f"cross-canonical -> {canon}")
    return {
        "indexable": indexable,
        "reasons": reasons,
        "canonical_self": canon_self,
        "x_robots_tag": xr or None,
        "mixed_content": mixed[:10],
    }


def attr(tag, name):
    m = re.search(name + r'\s*=\s*["\']([^"\']*)["\']', tag, re.I)
    return m.group(1).strip() if m else None


def meta(html, key, kind="name"):
    for m in re.finditer(r"<meta\b[^>]*>", html, re.I):
        t = m.group(0)
        if (attr(t, kind) or "").lower() == key.lower():
            return attr(t, "content")
    return None


def analyze(url, html):
    title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S) or [None, None])[1]
    title = re.sub(r"\s+", " ", title).strip() if title else None
    desc = meta(html, "description")
    canon = None
    for m in re.finditer(r"<link\b[^>]*>", html, re.I):
        if (attr(m.group(0), "rel") or "").lower() == "canonical":
            canon = attr(m.group(0), "href")
    robots = (meta(html, "robots") or "").lower()
    h1 = len(re.findall(r"<h1\b", html, re.I))
    lang = (
        attr(re.search(r"<html\b[^>]*>", html, re.I).group(0), "lang")
        if re.search(r"<html\b[^>]*>", html, re.I)
        else None
    )
    viewport = meta(html, "viewport")
    og = {k: meta(html, "og:" + k, "property") for k in ("title", "description", "image")}
    tw = meta(html, "twitter:card")
    jsonld = re.findall(r'"@type"\s*:\s*"([^"]+)"', html)
    favicon = bool(re.search(r'<link[^>]+rel=["\'][^"\']*icon', html, re.I))
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.I | re.S)
    words = len(re.sub(r"<[^>]+>", " ", text).split())
    imgs = re.findall(r"<img\b[^>]*>", html, re.I)
    no_alt = sum(1 for i in imgs if attr(i, "alt") is None)
    return {
        "title": title,
        "title_len": len(title) if title else 0,
        "description": desc,
        "desc_len": len(desc) if desc else 0,
        "canonical": canon,
        "noindex": "noindex" in robots,
        "h1_count": h1,
        "lang": lang,
        "viewport": bool(viewport),
        "og": {k: bool(v) for k, v in og.items()},
        "twitter_card": bool(tw),
        "jsonld_types": sorted(set(jsonld)),
        "favicon": favicon,
        "words": words,
        "imgs": len(imgs),
        "imgs_missing_alt": no_alt,
    }


def issues_for(p):
    """Return (issue_strings, fired_check_codes). Strings preserve the existing
    human/txt output; codes key into standards.py so each finding cites its
    standard + governance framework + why-it-fails."""
    out, codes = [], []

    def add(code, msg):
        out.append(msg)
        codes.append(code)

    if not p["title"]:
        add("seo-title", "missing <title>")
    elif not (10 <= p["title_len"] <= 65):
        add("seo-title", f"title length {p['title_len']} (aim 10-65)")
    if not p["description"]:
        add("seo-meta-description", "missing meta description")
    elif not (50 <= p["desc_len"] <= 165):
        add("seo-meta-description", f"description length {p['desc_len']} (aim 50-165)")
    if not p["canonical"]:
        add("seo-canonical", "no canonical link")
    if p["h1_count"] != 1:
        add("seo-h1", f"{p['h1_count']} <h1> (want exactly 1)")
    if not p["lang"]:
        add("seo-lang", "no html lang attribute")
    if not p["viewport"]:
        add("seo-viewport", "no viewport meta (mobile)")
    if not any(p["og"].values()):
        add("seo-open-graph", "no Open Graph tags (social sharing)")
    if not p["jsonld_types"]:
        add("seo-jsonld", "no JSON-LD structured data")
    if not p["favicon"]:
        add("seo-favicon", "no favicon link")
    if p["words"] < 200:
        add("seo-thin-content", f"thin content ({p['words']} words)")
    if p["imgs_missing_alt"]:
        add("seo-img-alt", f"{p['imgs_missing_alt']} image(s) missing alt")
    if p["noindex"]:
        add("seo-noindex", "META ROBOTS NOINDEX (page excluded from search)")
    return out, codes


def main():
    if len(sys.argv) < 2:
        print("usage: seo-check.py <out_dir>")
        return 2
    out = sys.argv[1].rstrip("/")
    urls = [u.strip() for u in open(os.path.join(out, "crawl", "urls.txt")) if u.strip()]
    cap = int(os.environ.get("SEO_MAX_PAGES", 300))
    urls = urls[:cap]
    dis = robots_disallows(out)
    pages, all_links = {}, set()
    for u in urls:
        try:
            st, hdrs, html = fetch(u)
            a = analyze(u, html)
            a["status"] = st
            a["issues"], a["issue_codes"] = issues_for(a)
            a["indexability"] = indexability(u, st, hdrs, a, html, dis)
            pages[u] = a
            for m in re.findall(r'<a\b[^>]*?href=["\']([^"\'#]+)', html, re.I):
                lu, _ = urldefrag(urljoin(u, m))
                if lu.startswith("http"):
                    all_links.add(lu)
        except Exception as e:
            pages[u] = {"status": f"ERR:{type(e).__name__}", "issues": ["fetch failed"]}

    # cross-page duplicates
    titles, descs = defaultdict(list), defaultdict(list)
    for u, p in pages.items():
        if p.get("title"):
            titles[p["title"]].append(u)
        if p.get("description"):
            descs[p["description"]].append(u)
    dup_titles = {t: us for t, us in titles.items() if len(us) > 1}
    dup_descs = {d[:50] + "...": us for d, us in descs.items() if len(us) > 1}

    # site-wide broken links (deduped, capped)
    broken = {}
    if os.environ.get("CHECK_LINKS", "1") == "1":
        BOT = ("medium.com", "facebook.com", "linkedin.com", "twitter.com", "x.com", "instagram.com", "youtube.com")
        for lu in sorted(all_links)[:400]:
            s = status(lu)
            if isinstance(s, int) and s >= 400:
                h = urlparse(lu).netloc.lower()
                broken[lu] = {"status": s, "likely_bot_block": any(h.endswith(b) for b in BOT)}
            elif isinstance(s, str):
                broken[lu] = {"status": s, "likely_bot_block": False}

    report = {
        "pages_checked": len(pages),
        "duplicate_titles": dup_titles,
        "duplicate_descriptions": dup_descs,
        "broken_links": broken,
        "pages": pages,
    }

    # indexability rollup (Google crawler)
    not_indexable = {
        u: p["indexability"]["reasons"]
        for u, p in pages.items()
        if p.get("indexability") and not p["indexability"]["indexable"]
    }
    cross_canon = {
        u: p["indexability"]["x_robots_tag"]
        for u, p in pages.items()
        if p.get("indexability") and not p["indexability"]["canonical_self"]
    }
    mixed = {
        u: p["indexability"]["mixed_content"]
        for u, p in pages.items()
        if p.get("indexability") and p["indexability"]["mixed_content"]
    }
    report["indexability"] = {
        "not_indexable": not_indexable,
        "cross_canonical": cross_canon,
        "mixed_content": mixed,
        "robots_disallows_googlebot": dis,
    }

    # ---- standards & best-practice citations for every check that fired ----
    fired = []
    for p in pages.values():
        fired.extend(p.get("issue_codes") or [])
    if dup_titles:
        fired.append("seo-duplicate-title")
    if dup_descs:
        fired.append("seo-duplicate-description")
    if any(not d["likely_bot_block"] for d in broken.values()):
        fired.append("seo-broken-links")
    if mixed:
        fired.append("seo-mixed-content")
    if not_indexable:
        fired.append("seo-indexability")
    report["standards"] = standards.applied(fired)
    json.dump(report, open(os.path.join(out, "5-seo.json"), "w"), indent=2)

    L = ["== SEO / METADATA / LINKS / INDEXABILITY =="]
    L.append(f"pages checked: {len(pages)}")
    L.append(
        f"[Google indexability] NOT indexable: {len(not_indexable)}; "
        f"cross-canonical: {len(cross_canon)}; pages with mixed content: {len(mixed)}"
    )
    for u, rs in list(not_indexable.items())[:10]:
        L.append(f"    NOT INDEXABLE {u} -> {', '.join(rs)}")
    for u, ms in list(mixed.items())[:5]:
        L.append(f"    MIXED CONTENT {u} -> {ms[0]} ...")
    if dis:
        L.append(f"    robots.txt Disallow (Googlebot/*): {dis}")
    L.append(
        f"missing <title>: {sum(1 for p in pages.values() if not p.get('title'))}; "
        f"missing description: {sum(1 for p in pages.values() if not p.get('description'))}; "
        f"no canonical: {sum(1 for p in pages.values() if not p.get('canonical'))}; "
        f"no Open Graph: {sum(1 for p in pages.values() if p.get('og') and not any(p['og'].values()))}"
    )
    L.append(f"duplicate titles: {len(dup_titles)}; duplicate descriptions: {len(dup_descs)}")
    real_broken = {u: d for u, d in broken.items() if not d["likely_bot_block"]}
    L.append(f"broken links: {len(real_broken)} (+{len(broken) - len(real_broken)} likely bot-blocked)")
    for u, d in list(real_broken.items())[:15]:
        L.append(f"    {d['status']}  {u}")
    L.append("")
    for u, p in pages.items():
        if p.get("issues"):
            L.append(f"[{u}]")
            for i in p["issues"]:
                L.append(f"    - {i}")
    if dup_titles:
        L.append("\nDuplicate titles:")
        for t, us in list(dup_titles.items())[:10]:
            L.append(f"    '{t[:50]}' on {len(us)} pages")
    if report["standards"]:
        L.append("\n-- WHY / STANDARDS (each finding above, with its referenced standard & framework) --")
        for code in report["standards"]:
            L.append("  - " + standards.format_text(code, "fail"))
    txt = "\n".join(L)
    open(os.path.join(out, "5-seo.txt"), "w").write(txt + "\n")
    print(txt[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
