#!/usr/bin/env python3
"""
Stage 5 — gate the stage 1 output before it costs money downstream.

Stage 1 already prints a per-section table, but it printed one for the 2025
paper too, and that run had 171 duplicate q_id values in it. Nothing looked
wrong. Stage 2 would have written 504 files for 675 questions and reconciled
cleanly, because reconciliation counts records, not files.

This script reads only what stage 1 already wrote — no PDF, no model, no cost —
and answers seven questions:

    questions found
    sections, and questions per section
    sections whose numbering is a clean 1 -> N run
    duplicate q_id count
    orphan content pages (in no question's span)
    rows carrying a paper label
    advertisement pages removed, and how many were colour-flagged

Two of them are hard stops:

    duplicate q_id above zero
    any section that is not a clean 1 -> N run

Everything else is reported and left to a human. A paper label is now only a
reporting column, so missing labels are worth knowing about and not worth
stopping for. Orphan pages are usually covers.

    python 05_gate_report.py -o out/
    python 05_gate_report.py -o out/ --json out/stage1_gate_report.json
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        sys.exit(f"missing {path} — run the earlier stages first")
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    ap = argparse.ArgumentParser(description="Gate the stage 1 output.")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--json", type=Path, default=None,
                    help="defaults to <outdir>/stage1_gate_report.json")
    ap.add_argument("--no-strict", action="store_true",
                    help="report the hard stops but exit 0 anyway")
    args = ap.parse_args()

    out = args.outdir
    rows = read_csv(out / "questions_index.csv")
    sections = json.loads((out / "sections.json").read_text(encoding="utf-8"))
    log = read_csv(out / "classification_log.csv")

    profile = {}
    ppath = out / "profile.json"
    if ppath.exists():
        profile = json.loads(ppath.read_text(encoding="utf-8"))

    failures: list[str] = []
    notes: list[str] = []

    # ---- duplicate q_id ---------------------------------------------------
    # HARD STOP. q_id is the stage 2 filename; two rows sharing one means one
    # question is overwritten by the other and never appears in the corpus.
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["q_id"]] = counts.get(r["q_id"], 0) + 1
    dupes = {k: v for k, v in counts.items() if v > 1}
    n_dupe_rows = sum(dupes.values()) - len(dupes)
    if dupes:
        worst = sorted(dupes.items(), key=lambda kv: -kv[1])[:5]
        failures.append(
            f"{len(dupes)} duplicated q_id covering {sum(dupes.values())} rows; "
            f"{n_dupe_rows} questions would be silently overwritten in stage 2.\n"
            + "".join(f"        {k!r} x{v}\n" for k, v in worst).rstrip())

    # ---- clean 1 -> N numbering per section -------------------------------
    # HARD STOP. A section that is not 1..N means the numbering pattern is
    # wrong for this publisher, or a page was dropped, or two sections were
    # merged. Any of those makes the index untrustworthy, and it is cheap to
    # check: across 2017, 2019, 2021, 2023 and 2025, 114 of 114 sections were
    # clean, so this fires only when something is genuinely broken.
    by_section: dict[str, list[int]] = {}
    for r in rows:
        by_section.setdefault(r["section_index"], []).append(int(r["q_no"]))
    clean, dirty = 0, []
    for sidx in sorted(by_section, key=lambda x: int(x)):
        nums = by_section[sidx]
        if nums == list(range(1, len(nums) + 1)):
            clean += 1
        else:
            missing = sorted(set(range(1, max(nums) + 1)) - set(nums))
            repeats = sorted({n for n in nums if nums.count(n) > 1})
            dirty.append(f"section {sidx}: {len(nums)} questions, "
                         f"range {min(nums)}-{max(nums)}"
                         + (f", missing {missing}" if missing else "")
                         + (f", repeated {repeats}" if repeats else ""))
    if dirty:
        failures.append("sections whose numbering is not a clean 1->N run:\n"
                        + "\n".join(f"        {d}" for d in dirty))

    # ---- orphan content pages --------------------------------------------
    # Reported, not fatal: covers and section dividers are legitimately in no
    # question's span.
    kept = sorted(int(f["page"]) for f in log if f["verdict"] in ("CONTENT", "REVIEW"))
    covered = set()
    for r in rows:
        a = int(r["original_page"])
        b = int(r["orig_page_end"] or a)
        covered.update(range(a, b + 1))
    orphans = [p for p in kept if p not in covered]

    # ---- paper labels ----------------------------------------------------
    labelled = sum(1 for r in rows if (r.get("paper") or "").strip())

    # ---- advertisement pages --------------------------------------------
    ads = [f for f in log if f["verdict"] == "AD"]
    review = [f for f in log if f["verdict"] == "REVIEW"]
    colour_cut = None
    if profile.get("content_colour_p95") is not None:
        colour_cut = max(profile["content_colour_p95"] * profile.get("colour_multiple", 10.0),
                         profile.get("colour_floor", 0.01))
    colour_flagged = 0
    colour_only = []
    if colour_cut is not None and log and "colour_frac" in log[0]:
        for f in ads:
            if float(f["colour_frac"] or 0) > colour_cut:
                colour_flagged += 1
        # A page the colour signal was decisive for: its score would have
        # fallen below the deletion threshold without colour. Worth listing,
        # because it is the set a false positive would hide in.
        delete_at = profile.get("ad_score_delete", 3)
        weight = profile.get("colour_score", 2)
        for f in ads:
            if (float(f["colour_frac"] or 0) > colour_cut
                    and int(f["ad_score"] or 0) - weight < delete_at):
                colour_only.append(int(f["page"]))

    sizes = sorted({s["n_questions"] for s in sections})
    if len(sizes) > 1:
        notes.append(f"section sizes vary: {sizes} — fine if the paper really "
                     f"is mixed, worth a look otherwise")
    if labelled < len(rows):
        notes.append(f"{len(rows) - labelled} of {len(rows)} rows carry no paper "
                     f"label; q_id no longer depends on it, so this is reporting "
                     f"only")
    if colour_cut is None:
        notes.append("colour signal was not calibrated for this document")
    if review:
        notes.append(f"{len(review)} pages classified REVIEW and KEPT: "
                     f"{[int(f['page']) for f in review][:20]}")

    report = {
        "outdir": str(out),
        "questions_found": len(rows),
        "sections": len(sections),
        "questions_per_section": {str(s["section_index"]): s["n_questions"]
                                  for s in sections},
        "sections_clean_1_to_n": clean,
        "sections_total": len(by_section),
        "duplicate_q_ids": len(dupes),
        "rows_lost_to_duplicate_q_ids": n_dupe_rows,
        "orphan_content_pages": orphans,
        "rows_with_paper_label": labelled,
        "ad_pages_removed": len(ads),
        "ad_pages_colour_flagged": colour_flagged,
        "ad_pages_colour_was_decisive": colour_only,
        "colour_threshold": colour_cut,
        "review_pages_kept": [int(f["page"]) for f in review],
        "hard_stops": len(failures),
        "passed": not failures,
    }

    w = 34
    print(f"\nstage 1 gate report — {out}")
    print("=" * 66)
    print(f"  {'questions found':<{w}} {len(rows)}")
    print(f"  {'sections':<{w}} {len(sections)}   "
          f"sizes {sizes}" + ("  (uniform)" if len(sizes) == 1 else "  (varies)"))
    print(f"  {'clean 1->N sections':<{w}} {clean} / {len(by_section)}"
          + ("" if clean == len(by_section) else "   <- HARD STOP"))
    print(f"  {'duplicate q_id':<{w}} {len(dupes)}"
          + ("" if not dupes else f"   <- HARD STOP ({n_dupe_rows} rows lost)"))
    print(f"  {'orphan content pages':<{w}} {len(orphans)}"
          + (f"   {orphans[:15]}" if orphans else ""))
    print(f"  {'rows with a paper label':<{w}} {labelled} / {len(rows)}")
    print(f"  {'advertisement pages removed':<{w}} {len(ads)}")
    if colour_cut is None:
        print(f"  {'of those, colour-flagged':<{w}} n/a (signal not calibrated)")
    else:
        print(f"  {'of those, colour-flagged':<{w}} {colour_flagged} "
              f"(threshold {colour_cut:.2%})")
        print(f"  {'colour was decisive for':<{w}} {len(colour_only)} "
              f"page(s)" + (f" {colour_only[:15]}" if colour_only else ""))
    print("=" * 66)

    if notes:
        print("\nnotes:")
        for n in notes:
            print(f"  - {n}")

    dest = args.json or (out / "stage1_gate_report.json")
    dest.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {dest}")

    if failures:
        print("\nHARD STOPS — stage 2 must not run on this index:")
        for f in failures:
            print(f"  - {f}")
        print("\nFix stage 1 and re-run it. Do not pass this index to stage 2: "
              "\n  duplicates overwrite each other in silence, and a broken "
              "numbering run\n  means the index does not describe the document.")
        if not args.no_strict:
            sys.exit(1)
    else:
        print("\ngate: PASS — no duplicate ids, every section numbers 1->N.")


if __name__ == "__main__":
    main()
