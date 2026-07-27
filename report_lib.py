#!/usr/bin/env python3
"""Shared report-rendering library for the Website Audit Toolkit.

Single source of truth for the report's look and components, so every site's
report is identical in formatting (see REPORT-TEMPLATE.md). A per-site
build_report.py imports this, loads the audit artifacts, and composes the authored
sections from these components — it must never hand-roll CSS or tables.

Provides: CSS, esc, badge, table, callout, sf (standards citations), figure,
status_table (§2 "show working"), sitemap (§3), techstack (§4), load, render_pdf.
"""

import base64
import html as H
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standards  # citations KB (sibling at repo root)

CSS = """
@page { size:A4; margin:15mm 13mm; }
* { box-sizing:border-box; }
body { font-family:'Helvetica Neue',Arial,sans-serif; color:#1a1f2b; font-size:10.5pt; line-height:1.5; }
h1 { font-size:25pt; margin:0 0 4px; color:#0b3d2e; }
h2 { font-size:15pt; color:#0b3d2e; border-bottom:2px solid #16a34a; padding-bottom:4px; margin:0 0 10px; page-break-before:always; page-break-after:avoid; }
h2:first-of-type { page-break-before:avoid; margin-top:18px; }  /* §1 shares the cover page */
h3 { font-size:11.5pt; color:#13334f; margin:16px 0 5px; }
p { margin:6px 0; }
.sub { color:#5a6675; font-size:10.5pt; }
.cover { padding:24px 0 14px; border-bottom:3px solid #16a34a; }
.badge { display:inline-block; padding:2px 8px; border-radius:4px; color:#fff; font-size:8.5pt; font-weight:bold; white-space:nowrap; }
.crit{background:#c0392b;} .high{background:#e67e22;} .med{background:#d4a017;} .good{background:#16a34a;} .info{background:#2980b9;}
table { border-collapse:collapse; width:100%; margin:8px 0; font-size:9.5pt; table-layout:fixed; page-break-inside:avoid; }
th,td { border:1px solid #cdd5df; padding:6px 8px; text-align:left; vertical-align:top; word-wrap:break-word; }
th { background:#0b3d2e; color:#fff; }
tr:nth-child(even) td { background:#f3f8f5; }
figure { margin:10px 0 14px; page-break-inside:avoid; border:1px solid #dde3ea; padding:7px; background:#fafbfc; text-align:center; }
figcaption { font-size:8.8pt; color:#5a6675; margin-top:6px; text-align:left; }
.fix { background:#eef7f0; border-left:4px solid #16a34a; padding:7px 11px; font-size:9.8pt; margin:7px 0; }
.fix code,.sf code,td code,p code,li code { font-family:Menlo,Consolas,monospace; font-size:8.8pt; background:#eef1f5; padding:1px 4px; border-radius:3px; }
.sf { background:#f3f7fc; border:1px solid #d4e2f2; border-left:4px solid #2980b9; padding:6px 11px; font-size:9pt; margin:7px 0 4px; page-break-inside:avoid; }
.sfh { font-weight:bold; color:#1c5a8f; font-size:8.6pt; letter-spacing:.04em; text-transform:uppercase; margin-bottom:3px; }
.sf ul { margin:0; padding-left:16px; } .sf li { margin:3px 0; }
.ref { color:#5577a0; font-size:8.4pt; word-break:break-all; }
ul { margin:5px 0 8px; padding-left:20px; } li { margin:2px 0; }
.cap { font-size:8.8pt; color:#5a6675; margin:6px 0 0; }
.caveat { background:#fff8ec; border:1px solid #f0d9a8; border-left:4px solid #d4a017; padding:7px 11px; font-size:9.3pt; margin:8px 0; page-break-inside:avoid; }
.keyfind { background:#fbeeee; border:1px solid #e8c9c9; border-left:4px solid #c0392b; padding:7px 11px; margin:8px 0; page-break-inside:avoid; }
.keyfind ul { margin:3px 0; }
.map { text-align:center; margin:10px 0 4px; }
.node { display:inline-block; border:1.5px solid #16a34a; border-radius:7px; padding:5px 9px; margin:4px; font-size:9pt; background:#f3f8f5; color:#0b3d2e; font-weight:bold; vertical-align:top; }
.node.root { background:#0b3d2e; color:#fff; font-size:10.5pt; padding:7px 14px; }
.node .rt { font-weight:normal; opacity:.8; }
.stem { width:0; height:14px; border-left:2px solid #9ec9b4; margin:0 auto; }
.tier { border-top:2px solid #9ec9b4; padding-top:10px; }
.node.art { border-color:#2980b9; background:#eef4fb; color:#13334f; }
.kids { margin-top:6px; }
.leaf { border:1px solid #b9cfe6; border-radius:5px; padding:3px 7px; margin:3px 0; font-size:8.2pt; font-weight:normal; background:#fff; color:#33506e; text-align:left; }
.leaf .al { color:#7a8696; }
"""

esc = H.escape


def badge(level, text):
    return f'<span class="badge {level}">{esc(text)}</span>'


def table(headers, rows, widths):
    cols = "".join(f'<col style="width:{w}">' for w in widths)
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><colgroup>{cols}</colgroup><tr>{head}</tr>{body}</table>"


def callout(kind, body):  # kind: caveat | keyfind | fix
    return f'<div class="{kind}">{body}</div>'


def figure(path, width, caption):
    p = Path(path)
    if not p.exists():
        return f'<p class="cap">[missing image: {esc(p.name)}]</p>'
    b64 = base64.b64encode(p.read_bytes()).decode()
    return (
        f"<figure><img style='width:{width}' src='data:image/png;base64,{b64}'/>"
        f"<figcaption>{caption}</figcaption></figure>"
    )


def sf(entries):
    """Standards & frameworks box (placed under a section's fix). entries: list of
    standards.py check-ids and/or literal {standard,url,frameworks} dicts."""
    items = []
    for e in entries:
        c = standards.cite(e) if isinstance(e, str) else e
        fw = " &middot; ".join(f"<b>{esc(k)}</b> {esc(str(v))}" for k, v in c["frameworks"].items())
        items.append(f"<li><b>{esc(c['standard'])}</b><br>{fw}<br><span class='ref'>{esc(c['url'])}</span></li>")
    return (
        f'<div class="sf"><div class="sfh">Standards &amp; frameworks referenced</div><ul>{"".join(items)}</ul></div>'
    )


# ---- deterministic sections (generated from artifacts) ----


def status_table(run_status):
    """§2 'show working' — every stage's ok/failed/skipped from 0-run-status.json."""
    badge_for = {"ok": ("good", "OK"), "failed": ("crit", "FAILED"), "skipped": ("info", "SKIPPED")}
    rows = []
    for s in run_status:
        lvl, lab = badge_for.get(s["status"], ("info", s["status"].upper()))
        rows.append([f"<code>{esc(s['stage'])}</code>", badge(lvl, lab), esc(s["detail"])])
    return table(["Stage", "Status", "Detail / why"], rows, ["20%", "14%", "66%"])


def sitemap(urls, host):
    paths = [u.replace(f"https://{host}", "").replace(f"http://{host}", "") or "/" for u in urls]
    arts = [p for p in paths if p.startswith("/articles/")]
    mains = [p for p in paths if not p.startswith("/articles/") and p != "/"]

    def art_label(p):
        parts = p.split("/")[-1].split("-")
        return f"{'-'.join(parts[:3])}<br><span class='al'>{esc(' '.join(parts[3:8]))}…</span>"

    main_nodes = "".join(f"<div class='node'>{esc(p)}</div>" for p in mains if p != "/articles")
    art_kids = "".join(f"<div class='leaf'>/articles/{art_label(p)}</div>" for p in arts)
    art_block = f"<div class='node art'>/articles<div class='kids'>{art_kids}</div></div>" if arts else ""
    return (
        f"<div class='map'><div class='node root'>{esc(host)} /<span class='rt'> · home</span></div>"
        f"<div class='stem'></div><div class='tier'>{main_nodes}{art_block}</div></div>"
    )


def techstack(rows):
    """rows: list of (layer, technology, evidence_html)."""
    return table(
        ["Layer", "Technology", "Evidence"],
        [[esc(layer), f"<b>{esc(tech)}</b>", ev] for layer, tech, ev in rows],
        ["22%", "30%", "48%"],
    )


# ---- data loading + render ----


def load(audit_dir):
    d = Path(audit_dir)

    def j(name):
        try:
            return json.loads((d / name).read_text())
        except Exception:
            return {}

    def t(name):
        try:
            return (d / name).read_text()
        except Exception:
            return ""

    urls = [u.strip() for u in t("crawl/urls.txt").splitlines() if u.strip()]
    return {
        "dir": d,
        "run_status": j("0-run-status.json") or [],
        "seo": j("5-seo.json"),
        "a11y": j("8-a11y.json"),
        "perf": j("9-perf.json"),
        "qa": j("6-qa.json"),
        "discovery": j("crawl/discovery.json"),
        "httpx": j("1-httpx.json"),
        "headers": t("3-headers.txt"),
        "dns": t("1-dns.txt"),
        "tls_ssllabs": t("2-tls-ssllabs.txt"),
        "urls": urls,
    }


def document(title, subtitle, body):
    return (
        f"<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head><body>"
        f"<div class='cover'><h1>{title}</h1><p class='sub'>{subtitle}</p></div>"
        f"{body}</body></html>"
    )


def render_pdf(html_doc, out_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.set_content(html_doc, wait_until="load")
        pg.pdf(
            path=str(out_path),
            format="A4",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        b.close()
    return out_path
