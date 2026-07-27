#!/usr/bin/env python3
"""Multi-page responsive/layout regression — stage 4 (scales to many pages).

Complements qa-check.py (which does the DEEP single-page template/asset review).
This runs the layout-critical subset across EVERY discovered page so page- or
template-specific breakage is caught site-wide. Reliable over fast: sequential,
capped, one browser reused, mobile screenshot captured only for pages WITH issues.

Per page (mobile 390 + desktop 1440): page horizontal scroll, crushed/uneven grid
columns, edge-clipped elements, hamburger size. Aggregates + flags template-wide
issues (same culprit class on many pages).

Usage: qa-site.py <out_dir>   (reads <out>/crawl/urls.txt)
Env: QA_MAX_PAGES(25) QA_VIEWPORTS("390,1440")
"""

import json
import os
import re
import sys
from collections import Counter
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

FORCE = (
    "document.querySelectorAll('[class*=reveal],[data-aos]').forEach(function(e){"
    "e.classList.add('in-view','aos-animate','visible');e.style.opacity=1;e.style.transform='none';});"
)
PROBE = r"""
() => {
  const vw = window.innerWidth;
  const vis = el => { const s=getComputedStyle(el), r=el.getBoundingClientRect();
    return s.display!=='none'&&s.visibility!=='hidden'&&+s.opacity>0&&r.width>0&&r.height>0; };
  const uneven = [];
  document.querySelectorAll('[class*=grid],[class*=track],[class*=cards],[class*=row],[class*=list]').forEach(el=>{
    const cs=getComputedStyle(el); if(cs.display!=='grid'&&cs.display!=='flex')return;
    const kids=[...el.children].filter(vis); if(kids.length<2)return;
    const ws=kids.map(k=>Math.round(k.getBoundingClientRect().width));
    const mn=Math.min(...ws),mx=Math.max(...ws);
    if(mx>0&&mn<mx*0.5&&mn<150) uneven.push({cls:(el.className||'').toString().split(' ')[0].slice(0,40),min:mn,max:mx});
  });
  const clipped=[];
  document.querySelectorAll('*').forEach(el=>{
    if(!vis(el))return; const cls=(el.className||'').toString();
    if(/skip|sr-only|visually-hidden|screen-reader/i.test(cls))return;
    const r=el.getBoundingClientRect();
    if(r.width<=vw&&(r.right>vw+2||(r.left<-2&&r.left>-300))){
      if(![...el.children].some(c=>{const cr=c.getBoundingClientRect();return cr.width<=vw&&(cr.right>vw+2||cr.left<-2);}))
        clipped.push({tag:el.tagName.toLowerCase(),cls:cls.split(' ')[0].slice(0,40),txt:(el.innerText||'').trim().slice(0,24)});
    }
  });
  let nav=null; const nt=document.querySelector('.nav-toggle,[class*=hamburger],[class*=menu-toggle],button[aria-expanded]');
  if(nt&&vis(nt)){const r=nt.getBoundingClientRect();nav={w:Math.round(r.width),h:Math.round(r.height)};}
  return {vw, scrollW:document.documentElement.scrollWidth, uneven, clipped:clipped.slice(0,15), nav};
}
"""


def slug(url):
    p = urlparse(url)
    s = (p.path.strip("/") or "home").replace("/", "_")
    return re.sub(r"[^a-zA-Z0-9_-]", "", s)[:50]


def main():
    if len(sys.argv) < 2:
        print("usage: qa-site.py <out_dir>")
        return 2
    out = sys.argv[1].rstrip("/")
    # Browser stages must reach the site directly: Chromium mis-handles the TLS-intercepting
    # egress proxy and RSTs HTTPS. The audit host has open outbound (see run-audit.sh header),
    # and --ignore-certificate-errors covers the transparent interception, so clear the proxy vars.
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ.pop(_v, None)
    urls = [u.strip() for u in open(os.path.join(out, "crawl", "urls.txt")) if u.strip()]
    cap = int(os.environ.get("QA_MAX_PAGES", 25))
    vps = [int(x) for x in os.environ.get("QA_VIEWPORTS", "390,1440").split(",")]
    urls = urls[:cap]
    shotdir = os.path.join(out, "qa-pages")
    os.makedirs(shotdir, exist_ok=True)
    pages = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--ignore-certificate-errors"])
        for u in urls:
            rec = {"viewports": {}, "issues": []}
            has_issue = False
            for w in vps:
                ctx = b.new_context(
                    viewport={"width": w, "height": 900}, ignore_https_errors=True, reduced_motion="reduce"
                )
                pg = ctx.new_page()
                try:
                    pg.goto(u, wait_until="networkidle", timeout=45000)
                    pg.evaluate(FORCE)
                    pg.wait_for_timeout(800)
                    d = pg.evaluate(PROBE)
                    hs = d["scrollW"] > w + 2
                    rec["viewports"][w] = {
                        "h_scroll": hs,
                        "crushed": d["uneven"],
                        "clipped": len(d["clipped"]),
                        "nav": d["nav"],
                    }
                    if hs:
                        rec["issues"].append(f"{w}px: horizontal scroll")
                        has_issue = True
                    for g in d["uneven"]:
                        rec["issues"].append(f"{w}px: crushed grid .{g['cls']} ({g['min']}px vs {g['max']}px)")
                        has_issue = True
                    if d["clipped"] and w == min(vps):
                        rec["issues"].append(f"{w}px: {len(d['clipped'])} edge-clipped element(s)")
                        has_issue = True
                    if d["nav"] and (d["nav"]["w"] < 44 or d["nav"]["h"] < 44) and w == min(vps):
                        rec["issues"].append(f"{w}px: nav toggle {d['nav']['w']}x{d['nav']['h']} (<44)")
                    if has_issue and w == min(vps):
                        pg.screenshot(path=os.path.join(shotdir, f"{slug(u)}-{w}.png"), full_page=True)
                except Exception as e:
                    rec["issues"].append(f"{w}px: load error {type(e).__name__}")
                ctx.close()
            pages[u] = rec
        b.close()

    # aggregate + template detection
    crushed_classes = Counter()
    pages_hscroll, pages_crushed, pages_clip = [], [], []
    for u, r in pages.items():
        for _w, v in r["viewports"].items():
            if v.get("h_scroll"):
                pages_hscroll.append(u)
            for g in v.get("crushed", []):
                crushed_classes[g["cls"]] += 1
            if v.get("crushed"):
                pages_crushed.append(u)
            if v.get("clipped"):
                pages_clip.append(u)
    agg = {
        "pages_checked": len(pages),
        "viewports": vps,
        "pages_with_horizontal_scroll": sorted(set(pages_hscroll)),
        "pages_with_crushed_grids": sorted(set(pages_crushed)),
        "crushed_grid_classes": dict(crushed_classes),
        "pages": pages,
    }
    json.dump(agg, open(os.path.join(out, "7-qa-site.json"), "w"), indent=2)

    L = ["== MULTI-PAGE RESPONSIVE REGRESSION =="]
    L.append(f"pages checked: {len(pages)} (cap {cap}); viewports: {vps}")
    L.append(f"pages with horizontal scroll: {len(set(pages_hscroll))}")
    L.append(f"pages with crushed grids: {len(set(pages_crushed))}")
    if crushed_classes:
        L.append("crushed grid classes (count = pages affected -> likely template-wide):")
        for c, n in crushed_classes.most_common():
            L.append(f"    .{c}  x{n}")
    L.append("")
    for u, r in pages.items():
        if r["issues"]:
            L.append(f"[{u}]")
            for i in dict.fromkeys(r["issues"]):
                L.append(f"    - {i}")
    if not any(r["issues"] for r in pages.values()):
        L.append("No multi-page responsive issues detected.")
    txt = "\n".join(L)
    open(os.path.join(out, "7-qa-site.txt"), "w").write(txt + "\n")
    print(txt[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
