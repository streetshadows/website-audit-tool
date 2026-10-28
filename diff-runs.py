#!/usr/bin/env python3
"""Run-to-run diff — final stage. Audit as regression check.

Extracts a small set of stable metrics from this run and from the previous run (which
run-audit.sh moved to <out>/previous-run/) and reports only what changed, so a re-audit
after a fix shows a short, expected diff instead of a full re-read. A first run, or a run
with no previous-run/, simply records "no baseline".

Metrics compared (all already in the artifacts): per-stage run status, SSL Labs grade,
security headers present, axe violation rule count, genuine contrast failures, perf
category scores, SEO not-indexable / broken-link / soft-404, and the over-fetch
delivered-but-not-rendered endpoint count.

Usage: diff-runs.py <out_dir>
Writes: <out>/11-diff.json, <out>/11-diff.txt
"""

import json
import os
import re
import sys


def jload(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def tload(path):
    try:
        return open(path).read()
    except Exception:
        return ""


def metrics(run_dir):
    """A flat {metric: value} snapshot of one run directory. Missing artifacts are skipped."""
    m = {}
    # Per-run bookkeeping rows aren't site facts and the current run's last rows (the diff
    # itself, run-end) are written only on exit, so comparing them would be spurious noise.
    skip_stages = {"run-identity", "run-end", "11-diff"}
    status = jload(os.path.join(run_dir, "0-run-status.json")) or []
    for st in status:
        if isinstance(st, dict) and st.get("stage") and st["stage"] not in skip_stages:
            m[f"stage:{st['stage']}"] = st.get("status")

    tls = tload(os.path.join(run_dir, "2-tls-ssllabs.txt"))
    grades = re.findall(r"\bgrade\s+([A-F][+-]?)", tls)
    if grades:
        m["tls:grade"] = "/".join(sorted(set(grades)))

    headers = tload(os.path.join(run_dir, "3-headers.txt")).lower()
    if headers:
        for h in (
            "strict-transport-security",
            "content-security-policy",
            "x-content-type-options",
            "x-frame-options",
            "referrer-policy",
            "permissions-policy",
        ):
            m[f"header:{h}"] = "present" if re.search(rf"^{re.escape(h)}:", headers, re.M) else "absent"
        m["header:access-control-allow-origin:*"] = (
            "present" if re.search(r"^access-control-allow-origin:\s*\*", headers, re.M) else "absent"
        )

    a11y = jload(os.path.join(run_dir, "8-a11y.json")) or {}
    if a11y:
        m["a11y:axe_rule_count"] = len(a11y.get("violations_aggregated") or {})
        m["a11y:axe_pages_scanned"] = a11y.get("axe_pages_scanned")
        cs = a11y.get("contrast_summary") or {}
        m["a11y:genuine_contrast_failures"] = cs.get("genuine_failures")

    perf = jload(os.path.join(run_dir, "9-perf.json")) or {}
    # perf-check stores per-page/strategy results; pull any category scores it recorded.
    for key, val in _flatten_perf(perf):
        m[key] = val

    seo = jload(os.path.join(run_dir, "5-seo.json")) or {}
    if seo:
        idx = seo.get("indexability") or {}
        m["seo:not_indexable"] = len(idx.get("not_indexable") or {})
        m["seo:broken_links"] = len(seo.get("broken_links") or {})
        m["seo:soft_404"] = bool((seo.get("soft_404") or {}).get("soft_404"))

    over = jload(os.path.join(run_dir, "4b-overfetch.json")) or {}
    if over:
        m["overfetch:delivered_not_rendered_endpoints"] = len(over.get("over_fetch") or [])

    return m


def _flatten_perf(perf):
    """Yield (metric_name, score) for Lighthouse category scores, wherever perf-check put them."""
    out = []

    def walk(node, path):
        if isinstance(node, dict):
            scores = node.get("scores") if isinstance(node.get("scores"), dict) else None
            if scores:
                for cat, val in scores.items():
                    out.append((f"perf:{path}:{cat}".replace("::", ":"), val))
            for k, v in node.items():
                if k != "scores":
                    walk(v, f"{path}:{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(perf, "")
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: diff-runs.py <out_dir>")
        return 2
    out = sys.argv[1].rstrip("/")
    prev_dir = os.path.join(out, "previous-run")
    cur = metrics(out)

    report = {"has_baseline": os.path.isdir(prev_dir), "changed": [], "added": [], "removed": []}
    if report["has_baseline"]:
        prev = metrics(prev_dir)
        for k in sorted(set(cur) | set(prev)):
            if k not in prev:
                report["added"].append({"metric": k, "now": cur[k]})
            elif k not in cur:
                report["removed"].append({"metric": k, "was": prev[k]})
            elif cur[k] != prev[k]:
                report["changed"].append({"metric": k, "was": prev[k], "now": cur[k]})
    report["metrics_now"] = cur
    json.dump(report, open(os.path.join(out, "11-diff.json"), "w"), indent=2)

    L = ["== RUN-TO-RUN DIFF (key metrics vs previous-run/) =="]
    if not report["has_baseline"]:
        L.append("no baseline: previous-run/ not present (first run into this OUT_DIR). Nothing to compare.")
    elif not (report["changed"] or report["added"] or report["removed"]):
        L.append("no change in any tracked metric since the previous run.")
    else:
        for c in report["changed"]:
            L.append(f"  CHANGED  {c['metric']}: {c['was']} -> {c['now']}")
        for a in report["added"]:
            L.append(f"  NEW      {a['metric']}: {a['now']}")
        for r in report["removed"]:
            L.append(f"  GONE     {r['metric']}: was {r['was']}")
    txt = "\n".join(L)
    open(os.path.join(out, "11-diff.txt"), "w").write(txt + "\n")
    print(txt[:2600])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
