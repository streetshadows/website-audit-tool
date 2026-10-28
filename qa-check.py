#!/usr/bin/env python3
"""Website QA checker — UI / image / spelling / responsive issues.

Drives the page in headless Chromium at several viewports and reports problems
that are visible to real users. v2 adds the checks the first version missed:

  * crushed / uneven grid columns  — a grid child far narrower than its siblings
    (e.g. a responsive grid that does not collapse to one column, so cards get
    squeezed to an unreadable sliver). THIS is the class of bug a naive
    "scrollWidth == clientWidth" check misses, because the grid still fits.
  * edge-clipped elements          — boxes that fit the viewport but are pushed
    partly off the left/right edge (clipped labels, etc.)
  * image issues                   — broken / missing alt / oversized / no dims
  * mobile tap targets < 44px, body fonts < 12px
  * broken links (social domains that bot-block are reported separately)
  * spelling (aspell en_GB) + a curated confusable-word scan (principle/principal,
    practice/practise, ...) because a dictionary cannot catch real-word misuse.

It also screenshots each flagged grid so a human/LLM can eyeball it — automated
geometry is necessary but NOT sufficient; always review the screenshots.

Usage: qa-check.py <url> <out_dir>
Writes <out>/6-qa.json, <out>/6-qa.txt, qa-<viewport>.png, qa-issue-*.png.
"""

import io
import json
import os
import re
import subprocess
import sys
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

try:
    from PIL import Image, ImageStat

    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

WIDTHS = {"mobile": 390, "tablet": 768, "laptop": 1280, "desktop": 1440}
FORCE_REVEAL = (
    "document.querySelectorAll('[class*=reveal],[data-aos]').forEach(function(e){"
    "e.classList.add('in-view','aos-animate','visible');"
    "e.style.opacity=1;e.style.transform='none';});"
)
SPELL_IGNORE = {
    "cybersecurity",
    "devsecops",
    "soc",
    "siem",
    "edr",
    "xdr",
    "mfa",
    "vpn",
    "api",
    "apis",
    "url",
    "html",
    "css",
    "saas",
    "iso",
    "nist",
    "essential",
    "acsc",
    "oscp",
    "cissp",
    "sscp",
    "comptia",
    "pentest",
    "pentesting",
    "sydney",
    "canberra",
    "australia",
    "australian",
    "ransomware",
    "phishing",
    "malware",
    "ttps",
    "ioc",
    "iocs",
    "ot",
    "iot",
    "ai",
    "ml",
    "grc",
    "vciso",
    "ciso",
    "mssp",
    "soar",
    "ueba",
    "cyber",
    "fintech",
    "roadmap",
    "roadmaps",
    "blockchain",
    "devops",
    "zero",
}
BOT_BLOCK_DOMAINS = (
    "medium.com",
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "youtube.com",
)
# Real-word confusables a dictionary can't catch. Reported for human review.
# Real-word confusables a dictionary can't catch. Kept tight to avoid noise
# (omits very common, usually-correct words like your/you're/affect/effect).
CONFUSABLES = [
    "principle",
    "principal",
    "practise",
    "practice",
    "complement",
    "compliment",
    "discreet",
    "discrete",
    "stationary",
    "stationery",
    "licence",
    "license",
    "council",
    "counsel",
]

PROBE_JS = r"""
() => {
  const vw = window.innerWidth;
  const visible = el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display!=='none' && s.visibility!=='hidden' && +s.opacity>0 && r.width>0 && r.height>0;
  };
  // crushed / uneven grid+flex columns: a child far narrower than its siblings
  const uneven = [];
  document.querySelectorAll('[class*=grid],[class*=track],[class*=cards],[class*=row],[class*=list]').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.display!=='grid' && cs.display!=='flex') return;
    const kids = [...el.children].filter(visible);
    if (kids.length < 2) return;
    const ws = kids.map(k => Math.round(k.getBoundingClientRect().width));
    const mn = Math.min(...ws), mx = Math.max(...ws);
    if (mx > 0 && mn < mx * 0.5 && mn < 150) {
      uneven.push({cls: (el.className||'').toString().slice(0,50),
        cols: cs.gridTemplateColumns.slice(0,70), childWidths: ws.slice(0,12),
        minW: mn, maxW: mx});
    }
  });
  // edge-clipped: box fits the viewport but is pushed partly off an edge
  const clipped = [];
  document.querySelectorAll('*').forEach(el => {
    if (!visible(el)) return;
    const cls = (el.className||'').toString();
    if (/skip|sr-only|visually-hidden|screen-reader/i.test(cls)) return;  // intentional a11y offscreen
    const r = el.getBoundingClientRect();
    // box fits the viewport but is pushed partly off an edge (ignore far-offscreen = intentional)
    if (r.width <= vw && (r.right > vw + 2 || (r.left < -2 && r.left > -300))) {
      // innermost only: skip if a child is also clipped
      if (![...el.children].some(c => { const cr=c.getBoundingClientRect();
            return cr.width<=vw && (cr.right>vw+2 || cr.left<-2); }))
        clipped.push({tag: el.tagName.toLowerCase(), cls:(el.className||'').toString().slice(0,40),
          txt:(el.innerText||'').trim().slice(0,30),
          left: Math.round(r.left), right: Math.round(r.right), w: Math.round(r.width)});
    }
  });
  const imgs = [...document.querySelectorAll('img')].map(im => {
    const r = im.getBoundingClientRect();
    return {src: im.currentSrc || im.src, alt: im.getAttribute('alt'),
      hasAltAttr: im.hasAttribute('alt'), natW: im.naturalWidth, natH: im.naturalHeight,
      dispW: Math.round(r.width), dispH: Math.round(r.height),
      hasDims: im.hasAttribute('width') && im.hasAttribute('height')};
  });
  const small_targets = [], small_fonts = [];
  document.querySelectorAll('a,button,input,select,textarea,[role=button]').forEach(el => {
    if (!visible(el)) return;
    const r = el.getBoundingClientRect();
    if (r.width < 44 || r.height < 44)
      small_targets.push({tag: el.tagName.toLowerCase(),
        txt:(el.innerText||el.value||'').trim().slice(0,30), w: Math.round(r.width), h: Math.round(r.height)});
  });
  document.querySelectorAll('p,li,span,a,small,figcaption').forEach(el => {
    if (!visible(el) || !(el.innerText||'').trim()) return;
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs && fs < 12) small_fonts.push({tag: el.tagName.toLowerCase(),
      px: Math.round(fs*10)/10, txt:(el.innerText||'').trim().slice(0,40)});
  });
  // hamburger / nav toggle size (mobile)
  let navToggle = null;
  const nt = document.querySelector('[class*=nav-toggle],[class*=hamburger],[class*=menu-toggle],button[aria-expanded],[aria-label*=menu i]');
  if (nt && visible(nt)) { const r = nt.getBoundingClientRect();
    navToggle = {cls:(nt.className||'').toString().slice(0,40), w: Math.round(r.width), h: Math.round(r.height)}; }
  // auto-scrolling / infinite-animation carousels (motion + readability concern)
  // NB reduced-motion can force iteration-count to 1, so don't require 'infinite';
  // an animated container holding several images is the marquee/carousel signal.
  const marquees = [];
  document.querySelectorAll('*').forEach(el => {
    const cs = getComputedStyle(el);
    if (cs.animationName !== 'none' && el.querySelectorAll('img').length >= 3)
      marquees.push({cls:(el.className||'').toString().slice(0,40), anim: cs.animationName,
        imgs: el.querySelectorAll('img').length});
  });
  // page theme darkness (for image-contrast clash check)
  const bg = getComputedStyle(document.body).backgroundColor || '';
  const m = bg.match(/\d+/g); let pageBright = 1;
  if (m && m.length >= 3) pageBright = (0.299*+m[0] + 0.587*+m[1] + 0.114*+m[2]) / 255;
  return {vw, scrollW: document.documentElement.scrollWidth, uneven,
    clipped: clipped.slice(0,20), imgs, small_targets: small_targets.slice(0,25),
    small_fonts: small_fonts.slice(0,25), navToggle, marquees: marquees.slice(0,5),
    pageBright: Math.round(pageBright*100)/100};
}
"""


def analyze_images(rp, img_urls, page_dark):
    """Edge-bleed (sliced strips) + theme-clash (bright images on a dark site)."""
    if not HAVE_PIL:
        return {"note": "Pillow not installed — image analysis skipped (pip install Pillow)"}, {}
    bleed, clash = {}, {}
    for u in sorted(img_urls):
        try:
            body = rp.request.get(u, timeout=15000).body()
            im = Image.open(io.BytesIO(body)).convert("RGB")
            w, h = im.size
            small = im.resize((50, 50))
            px = list(small.getdata())
            n = len(px)
            r = sum(p[0] for p in px) / n
            g = sum(p[1] for p in px) / n
            b = sum(p[2] for p in px) / n
            bright = round((0.299 * r + 0.587 * g + 0.114 * b) / 255, 2)
            # theme clash: a bright image on a dark theme
            if page_dark and bright > 0.5:
                clash[u] = {"size": f"{w}x{h}", "brightness": bright, "meanRGB": [round(r), round(g), round(b)]}
            # edge-bleed: wide strip with content on top/bottom edge
            if h and w / h >= 2.5:
                top = max(ImageStat.Stat(im.crop((0, 0, w, 2))).stddev)
                bot = max(ImageStat.Stat(im.crop((0, h - 2, w, h))).stddev)
                edges = [e for e, v in (("top", top), ("bottom", bot)) if v > 40]
                if edges:
                    bleed[u] = {
                        "size": f"{w}x{h}",
                        "content_at": edges,
                        "top_var": round(top, 1),
                        "bottom_var": round(bot, 1),
                    }
        except Exception:
            pass
    return bleed, clash


def spell_check(text):
    try:
        p = subprocess.run(
            ["aspell", "list", "--lang=en_GB", "--encoding=utf-8"],
            input=text,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return {"error": str(e)}
    misspelled, names = {}, {}
    for w in p.stdout.split():
        wl = w.lower().strip(".,:;!?'\"()")
        if not wl or wl in SPELL_IGNORE or len(wl) < 3 or any(c.isdigit() for c in wl):
            continue
        (names if (w[:1].isupper() or w.isupper()) else misspelled)[w if w[:1].isupper() else wl] = 1
    # confusable real-word scan with a little context
    confus = {}
    low = text.lower()
    for word in CONFUSABLES:
        for m in re.finditer(r"\b" + re.escape(word) + r"\b", low):
            ctx = text[max(0, m.start() - 25) : m.end() + 25].replace("\n", " ").strip()
            confus.setdefault(word, []).append(ctx)
    return {
        "likely_misspellings": list(misspelled.keys()),
        "names_acronyms_for_review": list(names.keys()),
        "confusables_for_review": confus,
    }


def main():
    if len(sys.argv) < 3:
        print("usage: qa-check.py <url> <out_dir>")
        return 2
    url, out = sys.argv[1], sys.argv[2].rstrip("/")
    report = {"url": url, "viewports": {}, "images": [], "links": {}, "spelling": {}}
    page_text, link_urls, img_urls = "", set(), set()
    issue_shots = []

    # Browser stages must reach the site directly: Chromium mis-handles the TLS-intercepting
    # egress proxy and RSTs HTTPS. The audit host has open outbound (see run-audit.sh header),
    # and --ignore-certificate-errors covers the transparent interception, so clear the proxy vars.
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ.pop(_v, None)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--ignore-certificate-errors"])
        for name, w in WIDTHS.items():
            ctx = browser.new_context(
                viewport={"width": w, "height": 900}, ignore_https_errors=True, reduced_motion="reduce"
            )
            pg = ctx.new_page()
            pg.goto(url, wait_until="networkidle", timeout=60000)
            pg.evaluate(FORCE_REVEAL)
            pg.wait_for_timeout(1200)
            data = pg.evaluate(PROBE_JS)
            pg.screenshot(path=f"{out}/qa-{name}-{w}.png", full_page=True)
            report["viewports"][name] = {
                "width": w,
                "page_horizontal_scroll": data["scrollW"] > w + 2,
                "crushed_or_uneven_grids": data["uneven"],
                "edge_clipped_elements": data["clipped"],
                "small_tap_targets": data["small_targets"] if name == "mobile" else "(mobile only)",
                "small_fonts": data["small_fonts"] if name == "mobile" else "(mobile only)",
                "nav_toggle": data["navToggle"] if name == "mobile" else "(mobile only)",
                "auto_scroll_carousels": data["marquees"],
                "page_brightness": data["pageBright"],
            }
            # screenshot each flagged grid (evidence) — mobile is where it matters most
            if name == "mobile":
                for idx, g in enumerate(data["uneven"]):
                    sel = "." + g["cls"].split()[0] if g["cls"].split() else None
                    try:
                        el = pg.query_selector(sel) if sel else None
                        if el:
                            el.scroll_into_view_if_needed()
                            pg.wait_for_timeout(300)
                            fn = f"{out}/qa-issue-grid-{idx}.png"
                            el.screenshot(path=fn)
                            issue_shots.append(fn)
                    except Exception:
                        pass
            if name == "desktop":
                report["images"] = data["imgs"]
                page_text = pg.evaluate("() => document.body.innerText")
                for a in pg.eval_on_selector_all("a[href]", "els=>els.map(e=>e.href)"):
                    if a.startswith("http"):
                        link_urls.add(a)
                for im in data["imgs"]:
                    if im["src"]:
                        img_urls.add(urljoin(url, im["src"]))
            ctx.close()

        api = browser.new_context(ignore_https_errors=True)
        rp = api.new_page()
        broken, bot_blocked = {}, {}
        for u in sorted(link_urls | img_urls):
            try:
                r = rp.request.get(u, timeout=15000)
                if r.status >= 400:
                    host = urlparse(u).netloc.lower()
                    (bot_blocked if any(host.endswith(d) for d in BOT_BLOCK_DOMAINS) else broken)[u] = r.status
            except Exception as e:
                broken[u] = f"error: {str(e)[:60]}"
        report["links"] = {
            "checked": len(link_urls | img_urls),
            "broken": broken,
            "likely_bot_blocked_verify_manually": bot_blocked,
        }
        page_dark = (report["viewports"]["desktop"]["page_brightness"] or 1) < 0.3
        bleed, clash = analyze_images(rp, img_urls, page_dark)
        report["image_edge_bleed"] = bleed
        report["image_theme_clash"] = clash
        api.close()
        browser.close()

    issues = []
    for im in report["images"]:
        flags = []
        if im["natW"] == 0:
            flags.append("BROKEN (naturalWidth=0)")
        if not im["hasAltAttr"]:
            flags.append("missing alt attribute")
        elif (im["alt"] or "").strip() == "":
            flags.append("empty alt (ok if decorative)")
        if im["natW"] and im["dispW"] and im["natW"] > im["dispW"] * 2.2:
            flags.append(f"oversized (natural {im['natW']}px vs shown {im['dispW']}px)")
        if not im["hasDims"]:
            flags.append("no width/height attrs (layout-shift risk)")
        if flags:
            issues.append({"src": (im["src"] or "")[:90], "flags": flags})
    report["image_issues"] = issues
    report["spelling"] = spell_check(page_text)
    report["issue_screenshots"] = issue_shots

    with open(f"{out}/6-qa.json", "w") as fh:
        json.dump(report, fh, indent=2)

    L = ["== WEBSITE QA REPORT =="]
    for name, v in report["viewports"].items():
        L.append(f"[{name} {v['width']}px] page horizontal scroll: {'BUG' if v['page_horizontal_scroll'] else 'ok'}")
        for g in v["crushed_or_uneven_grids"]:
            L.append(
                f"    !! CRUSHED/UNEVEN GRID <.{g['cls']}> cols='{g['cols']}' "
                f"childWidths={g['childWidths']} (min {g['minW']}px vs max {g['maxW']}px)"
            )
        for c in v["edge_clipped_elements"][:8]:
            L.append(
                f"    edge-clipped <{c['tag']} .{c['cls']}> '{c['txt']}' "
                f"left={c['left']} right={c['right']} w={c['w']} (viewport {v['width']})"
            )
    mv = report["viewports"].get("mobile", {})
    if isinstance(mv.get("small_tap_targets"), list):
        L.append(f"[mobile] small tap targets (<44px): {len(mv['small_tap_targets'])}")
        for t in mv["small_tap_targets"][:6]:
            L.append(f"    <{t['tag']}> '{t['txt']}' {t['w']}x{t['h']}")
    if isinstance(mv.get("small_fonts"), list):
        L.append(
            f"[mobile] small fonts (<12px): {len(mv['small_fonts'])} (e.g. "
            + ", ".join(f"{f['px']}px '{f['txt'][:20]}'" for f in mv["small_fonts"][:4])
            + ")"
        )
    nt = mv.get("nav_toggle")
    if isinstance(nt, dict):
        ok = nt["w"] >= 44 and nt["h"] >= 44
        L.append(
            f"[mobile] hamburger/nav toggle <.{nt['cls']}> {nt['w']}x{nt['h']}px "
            f"-> {'ok' if ok else 'BELOW 44x44 min (recommend >=48x48 with padding)'}"
        )
    mq = mv.get("auto_scroll_carousels") or []
    if mq:
        L.append(
            f"[ux] auto-scrolling/infinite-animation carousels: {len(mq)} "
            "(hard to read moving content + motion-accessibility concern; "
            "consider a fade/cross-fade carousel or static grid, pause control, larger logos)"
        )
        for m in mq:
            L.append(f"    <.{m['cls']}> animation={m['anim']} imgs={m['imgs']}")
    L.append(f"[images] {len(report['images'])} total, {len(issues)} with issues:")
    for it in issues:
        L.append(f"    {it['src']}\n        -> {', '.join(it['flags'])}")
    lk = report["links"]
    L.append(f"[links] checked {lk['checked']}, broken {len(lk['broken'])}")
    for u, s in lk["broken"].items():
        L.append(f"    BROKEN {s}  {u}")
    if lk["likely_bot_blocked_verify_manually"]:
        L.append(f"[links] {len(lk['likely_bot_blocked_verify_manually'])} likely bot-blocked (verify manually):")
        for u, s in lk["likely_bot_blocked_verify_manually"].items():
            L.append(f"    {s}  {u}")
    eb = report.get("image_edge_bleed", {})
    if eb and "note" not in eb:
        L.append(f"[images] edge-bleed (sliced/cropped strip images): {len(eb)}")
        for u, d in eb.items():
            L.append(
                f"    {u} ({d['size']}) content at {d['content_at']} "
                f"edge (top_var={d['top_var']}, bottom_var={d['bottom_var']})"
            )
    elif eb.get("note"):
        L.append(f"[images] {eb['note']}")
    tc = report.get("image_theme_clash", {})
    if tc:
        L.append(f"[images] theme clash — bright images on a dark site (brightness>0.5): {len(tc)}")
        for u, d in tc.items():
            L.append(
                f"    {u} ({d['size']}) brightness={d['brightness']} meanRGB={d['meanRGB']} "
                "(re-style to match the dark/cyan palette)"
            )
    sp = report["spelling"]
    L.append(f"[spelling] likely misspellings: {sp.get('likely_misspellings', [])}")
    conf = sp.get("confusables_for_review", {})
    if conf:
        L.append("[spelling] confusable real-words (review in context):")
        for w, ctxs in conf.items():
            L.append(f"    '{w}' x{len(ctxs)}: " + " | ".join(ctxs[:2]))
    L.append(f"[spelling] names/acronyms (review): {sp.get('names_acronyms_for_review', [])}")
    if issue_shots:
        L.append(f"[evidence] grid screenshots: {issue_shots}")
    L.append(
        "\nNOTE: geometry checks are necessary but not sufficient — always "
        "eyeball qa-mobile-390.png and qa-issue-*.png at full resolution."
    )

    txt = "\n".join(L)
    with open(f"{out}/6-qa.txt", "w") as fh:
        fh.write(txt + "\n")
    print(txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
