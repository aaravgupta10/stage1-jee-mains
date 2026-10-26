#!/usr/bin/env python3
"""
Stage 0 — calibrate a profile for this PDF, then stop and report.

Nothing is split or deleted here. This stage only works out, from the
document's own statistics, what the question-numbering pattern is, which
content labels exist, where the template watermark sits, what a normal
vector-op count looks like, and which pages are repeated advertisement
templates. It prints a confidence report and writes profile.json.

Run this once per new format. If the report looks right, the following
stages reuse profile.json and you never think about it again.

    python 00_calibrate.py input.pdf -o out/
"""

import argparse
from pathlib import Path

import pymupdf

from jeelib import calibrate


def main() -> None:
    ap = argparse.ArgumentParser(description="Derive a detection profile from a PDF.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--min-cluster", type=int, default=3,
                    help="how many repeats before a layout counts as a template")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(args.pdf)
    prof = calibrate(doc, min_cluster=args.min_cluster)
    pages = doc.page_count
    doc.close()

    s = prof.stats
    print(f"{args.pdf.name}  ({pages} pages)\n")

    print("question numbering")
    scores = s.get("question_pattern_scores", {})
    for name in sorted(scores, key=lambda k: -scores[k]["score"])[:4]:
        v = scores[name]
        flag = " <- chosen" if name == prof.question_pattern_name else ""
        print(f"  {name:<12} hits={v['hits']:>5} longest_chain={v['longest']:>4} "
              f"coherence={v['coherence']:>6.0%} score={v['score']:>6}{flag}")
    print(f"  regime: {prof.numbering_regime}  "
          f"({s.get('numbering', {}).get('total_markers', '?')} questions, "
          f"{s.get('numbering', {}).get('resets', '?')} resets)\n")

    print("content markers found")
    for k, v in (s.get("marker_hits") or {}).items():
        print(f"  {k:<18} {v} hits")
    if not s.get("marker_hits"):
        print("  (none)")
    print()

    print("thresholds calibrated from this document")
    wm = s.get("watermark")
    if prof.watermark_frac:
        print(f"  watermark band   {prof.watermark_frac}  "
              f"(frac {wm['frac']} on {wm['present_on']:.0%} of content pages)")
    else:
        print("  watermark band   disabled")
    d = s.get("draw_ops", {})
    print(f"  draw_ops_high    {prof.draw_ops_high}  "
          f"(content median {d.get('content_median')}, p99 {d.get('content_p99')})")
    col = s.get("colour")
    if col:
        print(f"  colour threshold {col['threshold']:.2%}  "
              f"(content median {col['content_median']:.2%}, "
              f"p95 {col['content_p95']:.2%}, max {col['content_max']:.2%}, "
              f"over {col['sampled_content_pages']} sampled content pages; "
              f"scores +{col['score_added']})")
    else:
        print("  colour signal    disabled (no content baseline could be measured)")
    print()

    print("repeated advertisement templates")
    clusters = s.get("ad_clusters") or {}
    for h, n in sorted(clusters.items(), key=lambda kv: -kv[1]):
        print(f"  {h}  {n} pages")
    print(f"  total pages in ad clusters: {len(prof.ad_cluster_pages)}")
    if not clusters:
        print("  (none — falling back to hyperlinks and keywords)")

    if prof.warnings:
        print("\nWARNINGS — check these before trusting the run:")
        for w in prof.warnings:
            print("  -", w)
    else:
        print("\nno warnings")

    dest = args.outdir / "profile.json"
    prof.save(dest)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
