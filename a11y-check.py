#!/usr/bin/env python3
"""Accessibility audit (WCAG) across pages — stage 9.

Primary engine: axe-core (injected from CDN) run on each discovered page.
Independent cross-check: contrast ratios are also computed directly from the
rendered DOM (WCAG formula) so the most common negative finding (low contrast)
is corroborated by a second, independent method — not just taken from axe.

Usage: a11y-check.py <out_dir>   (reads <out>/crawl/urls.txt)
Env: A11Y_MAX_PAGES(15) AXE_VER(4.10.2)
"""

import json
import os
import sys
import urllib.request
from collections import defaultdict

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import standards  # standards & best-practice knowledge base (sibling module)

FORCE = (
    "document.querySelectorAll('[class*=reveal],[data-aos]').forEach(function(e){"
    "e.classList.add('in-view','aos-animate','visible');e.style.opacity=1;e.style.transform='none';});"
)

# Independent contrast check (WCAG relative luminance) — does NOT use axe.
# Walks the ancestor chain for the effective background. If it meets a
# background-IMAGE (gradient/photo) before a solid colour, the contrast cannot be
# computed from colours alone — that text is reported as INDETERMINATE (needs a
# visual check) rather than emitting a bogus dark-on-dark 1:1 ratio. This is the
# fix for the gradient-button false positive.
CONTRAST_JS = r"""
() => {
  const lum = c => { const a=[c[0],c[1],c[2]].map(v=>{v/=255;return v<=0.03928?v/12.92:Math.pow((v+0.055)/1.055,2.4);});
    return 0.2126*a[0]+0.7152*a[1]+0.0722*a[2]; };
  const rgb = s => { const m=(s||'').match(/\d+(\.\d+)?/g); return m?m.map(Number):null; };
  // Returns {rgb} for a solid background, or {indeterminate, reason} when a
  // gradient/image background is encountered first (colour-only contrast invalid).
  const bgInfo = el => { let e=el; while(e){ const cs=getComputedStyle(e);
    const bi=cs.backgroundImage;
    if(bi && bi!=='none') return {indeterminate:true, reason:(/gradient/i.test(bi)?'gradient background':'image background')};
    const c=rgb(cs.backgroundColor);
    if(c&&(c.length<4||c[3]>0)&&!(c[0]===0&&c[1]===0&&c[2]===0&&c[3]===0)) return {rgb:c};
    e=e.parentElement; } return {rgb:[255,255,255]}; };
  const vis = el => { const s=getComputedStyle(el),r=el.getBoundingClientRect();
    return s.display!=='none'&&s.visibility!=='hidden'&&+s.opacity>0&&r.width>0&&r.height>0&&(el.innerText||'').trim(); };
  const fails=[]; const indeterminate=[]; const seen=new Set();
  document.querySelectorAll('p,span,a,li,h1,h2,h3,h4,button,small,label,td,div').forEach(el=>{
    if(!vis(el)) return; if([...el.children].some(c=>(c.innerText||'').trim())) return; // leaf text only
    const cs=getComputedStyle(el); const fg=rgb(cs.color); if(!fg) return;
    const fs=parseFloat(cs.fontSize), bold=(+cs.fontWeight>=700);
    const large=(fs>=24)||(fs>=18.66&&bold); const min=large?3.0:4.5;
    const txt=(el.innerText||'').trim().slice(0,40); const key=txt.slice(0,30)+fs;
    const bg=bgInfo(el);
    if(bg.indeterminate){ if(!seen.has(key)){ seen.add(key);
      indeterminate.push({txt:txt, fs:Math.round(fs*10)/10, color:cs.color, reason:bg.reason}); } return; }
    const L1=lum(fg)+0.05, L2=lum(bg.rgb)+0.05; const ratio=Math.max(L1,L2)/Math.min(L1,L2);
    if(ratio<min){ if(!seen.has(key)){ seen.add(key);
      fails.push({txt:txt, ratio:Math.round(ratio*100)/100, need:min, fs:Math.round(fs*10)/10,
        color:cs.color, bg:`rgb(${bg.rgb.slice(0,3)})`}); } }
  });
  return {fails:fails.slice(0,60), indeterminate:indeterminate.slice(0,40)};
}
"""


def main():
    if len(sys.argv) < 2:
        print("usage: a11y-check.py <out_dir>")
        return 2
    out = sys.argv[1].rstrip("/")
    # Browser + axe-core fetch must reach out directly: Chromium mis-handles the TLS-intercepting
    # egress proxy and RSTs HTTPS. The audit host has open outbound (see run-audit.sh header),
    # and --ignore-certificate-errors covers the transparent interception, so clear the proxy vars.
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        os.environ.pop(_v, None)
    urls = [u.strip() for u in open(os.path.join(out, "crawl", "urls.txt")) if u.strip()]
    cap = int(os.environ.get("A11Y_MAX_PAGES", 15))
    urls = urls[:cap]
    ver = os.environ.get("AXE_VER", "4.10.2")
    try:
        axe_src = (
            urllib.request.urlopen(f"https://cdn.jsdelivr.net/npm/axe-core@{ver}/axe.min.js", timeout=30)
            .read()
            .decode()
        )
    except Exception as e:
        print(f"could not fetch axe-core: {e}")
        axe_src = None

    pages = {}
    agg = defaultdict(lambda: {"impact": "", "help": "", "pages": 0, "nodes": 0})
    contrast_pages = {}  # genuine sub-threshold runs (solid background)
    contrast_indeterminate = {}  # gradient/image bg — colour-only contrast invalid
    errors = []  # pages that failed to load/scan (flagged, not swallowed)
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--ignore-certificate-errors"])
        for u in urls:
            ctx = b.new_context(viewport={"width": 1280, "height": 900}, ignore_https_errors=True)
            pg = ctx.new_page()
            rec = {"violations": [], "error": None}
            try:
                pg.goto(u, wait_until="networkidle", timeout=45000)
                pg.evaluate(FORCE)
                pg.wait_for_timeout(700)
                if axe_src:
                    pg.add_script_tag(content=axe_src)
                    res = pg.evaluate("async () => await axe.run(document, {resultTypes:['violations']})")
                    for v in res.get("violations", []):
                        rec["violations"].append(
                            {
                                "id": v["id"],
                                "impact": v.get("impact"),
                                "help": v.get("help"),
                                "nodes": len(v.get("nodes", [])),
                            }
                        )
                        a = agg[v["id"]]
                        a["impact"] = v.get("impact")
                        a["help"] = v.get("help")
                        a["pages"] += 1
                        a["nodes"] += len(v.get("nodes", []))
                # independent contrast check (separates genuine fails from gradient/image-bg indeterminate)
                c = pg.evaluate(CONTRAST_JS)
                if c.get("fails"):
                    contrast_pages[u] = c["fails"]
                if c.get("indeterminate"):
                    contrast_indeterminate[u] = c["indeterminate"]
                rec["contrast_independent"] = len(c.get("fails", []))
                rec["contrast_indeterminate"] = len(c.get("indeterminate", []))
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {str(e)[:80]}"
                errors.append({"url": u, "error": rec["error"]})
            pages[u] = rec
            ctx.close()
        b.close()

    n_fail = sum(len(v) for v in contrast_pages.values())
    n_indet = sum(len(v) for v in contrast_indeterminate.values())
    # Deterministic quality flags — thresholds that say when a human/AI visual pass is warranted.
    flags = []
    if not axe_src:
        flags.append("axe-core could not be fetched — only the independent contrast check ran.")
    if errors:
        flags.append(f"{len(errors)} page(s) failed to load/scan — results incomplete (see 'errors').")
    if n_indet:
        flags.append(
            f"{n_indet} text run(s) sit on a gradient/image background — contrast is NOT "
            "auto-determinable; VISUAL CHECK REQUIRED (these are not counted as failures)."
        )
    if n_fail:
        flags.append(f"{n_fail} text run(s) fail WCAG AA contrast on a solid background (genuine).")

    report = {
        "engine": f"axe-core {ver}",
        "pages_checked": len(pages),
        "violations_aggregated": dict(agg),
        "contrast_independent_check": contrast_pages,
        "contrast_indeterminate": contrast_indeterminate,
        "contrast_summary": {"genuine_failures": n_fail, "indeterminate_gradient_or_image": n_indet},
        "errors": errors,
        "flags": flags,
        "pages": pages,
    }

    # ---- standards & best-practice citations: each axe rule id resolves to its
    # WCAG success criterion + governance framework; add the independent contrast
    # cross-check so a confirmed contrast defect is cited as such ----
    fired = list(agg.keys())
    if contrast_pages:
        fired.append("a11y-contrast-independent")
    report["standards"] = standards.applied(fired)
    json.dump(report, open(os.path.join(out, "8-a11y.json"), "w"), indent=2)

    order = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3, None: 4}
    L = ["== ACCESSIBILITY (axe-core + independent contrast cross-check) =="]
    L.append(f"engine: axe-core {ver}; pages checked: {len(pages)}")
    if not agg:
        L.append("axe-core: no violations (or axe unavailable).")
    else:
        L.append("axe-core violations (id | impact | pages | nodes | help):")
        for vid, a in sorted(agg.items(), key=lambda kv: order.get(kv[1]["impact"], 4)):
            L.append(f"    {vid} | {a['impact']} | {a['pages']} pages | {a['nodes']} nodes | {a['help']}")
    # contrast cross-check (genuine failures only — solid backgrounds)
    L.append(
        f"\nIndependent contrast cross-check: {n_fail} GENUINE low-contrast run(s) on "
        f"{len(contrast_pages)} page(s) (solid background; corroborates axe 'color-contrast')"
    )
    for u, items in list(contrast_pages.items())[:3]:
        L.append(f"  [{u}]")
        for it in items[:6]:
            L.append(
                f"    ratio {it['ratio']} (need {it['need']}) {it['fs']}px '{it['txt']}'  {it['color']} on {it['bg']}"
            )
    # indeterminate (gradient/image bg) — reported separately, NOT counted as failures
    L.append(
        f"\nIndeterminate (gradient/image background — VISUAL CHECK REQUIRED, not a failure): "
        f"{n_indet} run(s) on {len(contrast_indeterminate)} page(s)"
    )
    for _u, items in list(contrast_indeterminate.items())[:2]:
        for it in items[:4]:
            L.append(f"    [{it['reason']}] {it['fs']}px '{it['txt']}'  text {it['color']}")
    if errors:
        L.append(f"\nERRORS: {len(errors)} page(s) failed to scan:")
        for e in errors[:5]:
            L.append(f"    {e['url']} -> {e['error']}")
    if flags:
        L.append("\n-- QUALITY FLAGS --")
        for f in flags:
            L.append(f"    [!] {f}")
    if report["standards"]:
        L.append("\n-- WHY / STANDARDS (each rule above mapped to WCAG SC + governance framework) --")
        for code in report["standards"]:
            L.append("  - " + standards.format_text(code, "fail"))
    txt = "\n".join(L)
    open(os.path.join(out, "8-a11y.txt"), "w").write(txt + "\n")
    print(txt[:2800])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
