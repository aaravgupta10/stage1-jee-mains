#!/usr/bin/env python3
"""
Stage 1 — classify every page as CONTENT / AD / REVIEW using profile.json.

Reads the profile written by 00_calibrate.py, so no thresholds are baked in.
Nothing is deleted here; this script only decides and logs why.

    python 01_classify.py input.pdf -o out/
"""

import argparse
import csv
import json
from pathlib import Path

import pymupdf

from jeelib import Profile, calibrate, extract_features, classify_pages, ad_pages, kept_pages

FIELDS = [
    "page", "verdict", "ad_score", "reason", "chars", "markers", "ad_keywords",
    "links", "draw_ops", "n_images", "img_frac_total", "img_frac_largest",
    "colour_frac",
    "has_watermark", "fp", "fp_cluster_size", "in_ad_cluster",
    "outside_question_range", "question_nums", "subjects", "paper", "head",
]


def main() -> None:
    ap = argparse.ArgumentParser(description="Classify pages as content or advertisement.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--profile", type=Path, default=None,
                    help="defaults to <outdir>/profile.json; auto-calibrates if absent")
    ap.add_argument("--ad-score-delete", type=int, default=None)
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(args.pdf)

    ppath = args.profile or (args.outdir / "profile.json")
    if ppath.exists():
        prof = Profile.load(ppath)
        print(f"using profile {ppath} (pattern '{prof.question_pattern_name}')")
    else:
        print("no profile found — calibrating on the fly")
        prof = calibrate(doc)
        prof.save(args.outdir / "profile.json")
    if args.ad_score_delete:
        prof.ad_score_delete = args.ad_score_delete

    feats = classify_pages([extract_features(p, prof) for p in doc], prof)
    doc.close()

    with (args.outdir / "classification_log.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for f in feats:
            w.writerow({k: f.as_row()[k] for k in FIELDS})

    plan = {
        "source_pdf": str(args.pdf),
        "total_pages": len(feats),
        "keep_pages": kept_pages(feats),
        "ad_pages": ad_pages(feats),
        "review_pages": [f.page for f in feats if f.verdict == "REVIEW"],
    }
    (args.outdir / "split_plan.json").write_text(json.dumps(plan, indent=2))

    counts = {v: sum(1 for f in feats if f.verdict == v) for v in ("CONTENT", "AD", "REVIEW")}
    print(f"pages={len(feats)}  content={counts['CONTENT']}  ads={counts['AD']}  "
          f"review={counts['REVIEW']}")
    if plan["review_pages"]:
        print(f"REVIEW pages (kept, check them): {plan['review_pages']}")


if __name__ == "__main__":
    main()
