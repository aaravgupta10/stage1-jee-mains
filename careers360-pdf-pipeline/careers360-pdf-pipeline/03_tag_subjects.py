#!/usr/bin/env python3
"""
Stage 3 — tag every question with its paper/shift and subject.

v2 changes, driven by the fact that question counts and shift counts vary
between papers from the same publisher:

  * Section size is DERIVED, not asserted. v1 took --expect-per-section 30
    and aborted otherwise; that fails the moment you feed it a 25-question
    or 45-question paper. Sections are now found from numbering resets and
    their sizes reported. --expect-per-section still exists but is opt-in,
    for when you do know the answer and want the extra guard.
  * Shift/paper count is derived the same way. No assumption of three.
  * Sections with no recognised subject name fall back to a positional
    label ("section_4, pages 77-123, Q1-30") rather than failing. An
    unlisted subject degrades to something usable you can relabel by hand.
  * Continuous numbering (1..1000, no resets) is detected and handled by
    falling back to label changes for boundaries.

Two independent signals are still cross-checked: printed section headers,
and question-number resets. Requiring them to agree turns a silent wrong
label into a loud failure.

    python 03_tag_subjects.py -o out/
    python 03_tag_subjects.py -o out/ --expect-per-section 30   # optional guard
"""

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import pymupdf

from jeelib import Profile, ordered_events


def main() -> None:
    ap = argparse.ArgumentParser(description="Tag questions by paper/shift/subject.")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--pdf", type=Path, default=None)
    ap.add_argument("--expect-per-section", type=int, default=None,
                    help="optional guard; omit to let section sizes vary")
    ap.add_argument("--expect-sections", type=int, default=None,
                    help="optional guard on the number of sections")
    ap.add_argument("--no-strict", action="store_true")
    args = ap.parse_args()

    prof = Profile.load(args.outdir / "profile.json")
    content_pdf = args.pdf or (args.outdir / "content.pdf")
    page_map = {
        int(r["content_pdf_page"]): int(r["original_page"])
        for r in csv.DictReader((args.outdir / "page_map.csv").open(encoding="utf-8"))
    }

    doc = pymupdf.open(content_pdf)
    page_texts = [(i + 1, p.get_text()) for i, p in enumerate(doc)]
    last_page = doc.page_count
    doc.close()

    paper = subject = ""
    hits: list[dict] = []
    problems: list[str] = []
    notes: list[str] = []

    for pno, text in page_texts:
        for kind, val in ordered_events(text, prof):
            if kind == "paper":
                paper = val
            elif kind == "subject":
                subject = val
            else:
                hits.append({"q_no": int(val), "paper": paper, "subject": subject,
                             "content_page": pno,
                             "original_page": page_map.get(pno, pno)})

    if not hits:
        raise SystemExit("no question markers found — wrong pdf, or profile mismatch")

    # collapse repeated markers for the same question on continuation pages
    questions: list[dict] = []
    for h in hits:
        if questions and (questions[-1]["q_no"], questions[-1]["subject"],
                          questions[-1]["paper"]) == (h["q_no"], h["subject"], h["paper"]):
            continue
        questions.append(dict(h, page_end=h["content_page"]))

    for i, q in enumerate(questions):
        nxt = questions[i + 1]["content_page"] if i + 1 < len(questions) else last_page + 1
        # A question's span runs UP TO AND INCLUDING the page the next question
        # starts on, not the page before it. A question and the next one
        # routinely share a page: the options, printed answer or printed
        # solution of this question continue past its own last full page and
        # finish above the next question's marker. Ending at `nxt - 1` cut that
        # tail off, so the answer and solution were simply missing from the
        # images sent for the question.
        #
        # The last question of a section needs no special case - it runs on to
        # wherever the next section starts. The last question of the DOCUMENT
        # does: `nxt` is `last_page + 1` there, so clamp, or the span points one
        # page past the end of the document.
        q["page_end"] = min(max(q["content_page"], nxt), last_page)
        q["orig_page_end"] = page_map.get(q["page_end"], q["page_end"])

    # ---- boundaries from two independent signals ------------------------
    reset_bounds = [0] + [i for i in range(1, len(questions))
                          if questions[i]["q_no"] <= questions[i - 1]["q_no"]]
    label_bounds = [0] + [i for i in range(1, len(questions))
                          if (questions[i]["paper"], questions[i]["subject"])
                          != (questions[i - 1]["paper"], questions[i - 1]["subject"])]

    if prof.numbering_regime == "continuous" or len(reset_bounds) == 1:
        bounds_src = "labels (numbering is continuous, no resets to use)"
        bounds = label_bounds
        if len(label_bounds) == 1:
            problems.append("numbering never resets and no section labels found — "
                            "cannot infer section boundaries; pass --expect-sections "
                            "or extend SUBJECT_CANON")
    else:
        bounds_src = "numbering resets, cross-checked against labels"
        bounds = reset_bounds
        # A coarse "the two boundary lists differ at all" check used to live
        # here and treated any disagreement as fatal. That is too blunt: on
        # the 2024 paper the subject name for a new section is sometimes
        # printed as a footer under the *first* question's solution instead
        # of a header above it (page 171: "Q. 1 ... Solution: ... Chemistry"),
        # so exactly one question per affected section inherits the previous
        # section's subject and the two boundary lists land one question
        # apart. The real cross-check now happens per-section below, against
        # the block's majority subject/paper — which also catches genuine
        # disagreements (no clear majority), just without false-alarming on
        # this specific label-timing artifact.

    # ---- build sections; sizes are derived, not assumed -----------------
    sections = []
    edges = bounds + [len(questions)]
    for a, b in zip(edges, edges[1:]):
        block = questions[a:b]
        idx = len(sections) + 1
        subj_counts = Counter(q["subject"] for q in block if q["subject"])
        paper_counts = Counter(q["paper"] for q in block if q["paper"])
        if subj_counts:
            name, name_hits = subj_counts.most_common(1)[0]
            stray = len(block) - name_hits
            if stray:
                where = f"pages {block[0]['original_page']}-{block[-1]['orig_page_end']}"
                if stray > max(1, len(block) // 10):
                    problems.append(
                        f"section {idx} ({where}): subject label disagrees on "
                        f"{stray}/{len(block)} questions, no clear majority "
                        f"({dict(subj_counts)}) — needs a human look")
                else:
                    notes.append(
                        f"section {idx} ({where}): {stray} question(s) carried "
                        f"a stale subject label, probably printed after rather "
                        f"than before the transition question; used the "
                        f"block's majority subject '{name}' ({dict(subj_counts)})")
        else:
            name = f"section_{idx}"
            problems.append(f"section {idx} (pages "
                            f"{block[0]['original_page']}-{block[-1]['orig_page_end']}) "
                            f"has no recognised subject name; labelled '{name}'")
        paper_label = paper_counts.most_common(1)[0][0] if paper_counts else ""
        for q in block:
            q["section_index"] = idx
            q["subject_label"] = name
            q["paper"] = paper_label
            # A3: the id is built from the section index, NOT the paper label.
            #
            # The paper label comes out of a regex over the text layer and is
            # only as good as that regex. On the 2025 paper it produced
            # fragments like "Set B :" for several different sections at once,
            # so 675 questions collapsed onto 504 ids: two different questions
            # both became "Set_B_:|Mathematics|Q005". q_id is the filename in
            # stage 2, so those questions overwrite each other and vanish
            # without an error.
            #
            # section_index is derived from numbering resets cross-checked
            # against headers. It is sequential and unique by construction, so
            # it cannot collide however bad the labels are. The paper label is
            # still carried in its own column for reporting; it is simply no
            # longer load-bearing.
            q["q_id"] = f"S{idx:02d}|{name}|Q{q['q_no']:03d}".replace(" ", "_")
        nums = [q["q_no"] for q in block]
        missing = sorted(set(range(min(nums), max(nums) + 1)) - set(nums))
        sections.append({
            "section_index": idx,
            "paper": paper_label,
            "subject": name,
            "n_questions": len(block),
            "q_range": f"{min(nums)}-{max(nums)}",
            "missing_numbers": missing,
            "content_page_start": block[0]["content_page"],
            "content_page_end": block[-1]["page_end"],
            "original_page_start": block[0]["original_page"],
            "original_page_end": block[-1]["orig_page_end"],
        })
        if missing:
            problems.append(f"section {idx} ({name}) missing Q {missing}")
        if args.expect_per_section and len(block) != args.expect_per_section:
            problems.append(f"section {idx} ({name}) has {len(block)} questions, "
                            f"expected {args.expect_per_section}")

    if args.expect_sections and len(sections) != args.expect_sections:
        problems.append(f"found {len(sections)} sections, expected {args.expect_sections}")

    # ---- informational: are section sizes uniform? ----------------------
    sizes = sorted({s["n_questions"] for s in sections})
    papers = sorted({s["paper"] for s in sections if s["paper"]})

    cols = ["q_id", "section_index", "paper", "subject_label", "q_no",
            "content_page", "page_end", "original_page", "orig_page_end"]
    with (args.outdir / "questions_index.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(questions)
    (args.outdir / "sections.json").write_text(json.dumps(sections, indent=2))

    print(f"boundaries from: {bounds_src}\n")
    print(f"{'#':>2} {'paper / shift':<38} {'subject':<14} {'Qs':>4} {'range':>8}  orig pages")
    for s in sections:
        print(f"{s['section_index']:>2} {s['paper'][:38]:<38} {s['subject']:<14} "
              f"{s['n_questions']:>4} {s['q_range']:>8}  "
              f"{s['original_page_start']}-{s['original_page_end']}")
    print(f"\nquestions: {len(questions)}   sections: {len(sections)}   "
          f"papers/shifts: {len(papers)}")
    print(f"section sizes present: {sizes}"
          + ("  (uniform)" if len(sizes) == 1 else "  (varies — fine, just noting it)"))

    if notes:
        print("\nNOTES (auto-corrected, not fatal):")
        for n in notes:
            print("  -", n)

    if problems:
        print("\nVALIDATION PROBLEMS:")
        for p in problems:
            print("  -", p)
        if not args.no_strict:
            raise SystemExit(1)
    elif not notes:
        print("validation: OK (signals agree, no numbering gaps, every section named)")
    else:
        print("validation: OK, with the auto-corrections noted above")


if __name__ == "__main__":
    main()
