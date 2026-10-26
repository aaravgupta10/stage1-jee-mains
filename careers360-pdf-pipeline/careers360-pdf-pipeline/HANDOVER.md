# Handover — Careers360 question-paper PDF pipeline

Splits question-paper PDFs into content and advertisement halves, renders the
content at high DPI, tags every question with its paper/shift and subject, and
gates the index before anything expensive runs. `README.md` is the real
documentation; this file is just what you need on day one.

**Date:** 18 September 2026. Supersedes the 14 September version, which
predates the colour signal, the `q_id` change and stage 5.

For the cross-stage view — how this feeds stage 2, and what not to do — read
`PIPELINE_TEAM_HANDOVER.md` in `Downloads\` first. A complete verified run of
both stages is at `C:\Users\yoyos\Downloads\jee2025_full\`.

## Day one

```bash
pip install -r requirements.txt          # pymupdf + psutil + numpy
python run_all.py input.pdf -o out/ --skip-render     # ~40 s, read the report
python run_all.py input.pdf -o out/ --dpi 600         # add the images
```

For a paper from a publisher you have already reviewed, skip calibration:

```bash
python run_all.py next.pdf -o out2/ --profile profiles/careers360.json --dpi 600
```

**Reuse a profile only across papers of the same format.** A profile carries
that document's watermark band, vector-op threshold, colour baseline and
advertisement fingerprints. The 2025 paper was once processed with the 2018
profile: it wore 2018's watermark band, so 14 pages fell into REVIEW for the
wrong reason, and 2018's advertisement fingerprints deleted a section divider
as an advertisement. Nothing failed loudly. If in doubt, calibrate.

Install `psutil`. It is listed as optional, but without it the memory
protection in stages 2 and 4 silently switches off on Windows and macOS, and
those are the platforms where the crashes it prevents actually happen.

`numpy` is used only to make the colour measurement fast. Without it the
pipeline still works and gives identical answers, about a hundred times slower
on that one step.

## The stages

| | script | costs |
|---|---|---|
| 0 | `00_calibrate.py` — derive a profile from the document's own statistics | seconds |
| 1 | `01_classify.py` — every page CONTENT / AD / REVIEW, with reasons | seconds |
| 2 | `02_split.py` — write `content.pdf` and `removed_ads.pdf` | seconds |
| 3 | `03_tag_subjects.py` — paper, subject, `q_id`, page spans | seconds |
| **5** | **`05_gate_report.py` — gate the index. Two hard stops.** | free |
| 4 | `04_render.py` — 600 DPI page images | ~35 min |

`run_all.py` runs stage 5 **before** stage 4 on purpose. Rendering is the
expensive stage and there is no point spending it on an index the next pipeline
will lose questions from.

## Stage 5, and why it exists

```bash
python 05_gate_report.py -o out/
```

**Hard stops:** any duplicate `q_id`, or any section whose numbering is not a
clean 1→N run. Exit code 1 on either.

Everything else is reported, not enforced: questions found, sections and their
sizes, orphan content pages, rows carrying a paper label, advertisement pages
removed and how many were colour-flagged.

This exists because a 2025 run produced **171 duplicate `q_id` values**, printed
a tidy section table, and reported success. `q_id` is the filename in the
downstream stage, so those questions would have overwritten each other in
silence — and the downstream reconciliation cannot catch it, because it counts
records, not files.

`q_id` is now `S07|Physics|Q011`, built from `section_index`, which is derived
and unique by construction. It is no longer built from the paper label, which
is a regex over the text layer and was producing `Set B :` for several sections
at once. The paper label is still written to its own column for reporting.

## Confirming your copy works

`reference_output/` holds the artefacts from a verified run of the JEE Main
2018 official papers (393 pages, 270 questions, 9 sections, 3 shifts),
**regenerated with the current code**. Run the pipeline on that same PDF and
the files should match:

```bash
python run_all.py "JEE Main 2018 ....pdf" -o out/ --skip-render
diff out/questions_index.csv    reference_output/questions_index.csv   # expect nothing
diff out/sections.json          reference_output/sections.json
diff out/classification_log.csv reference_output/classification_log.csv
```

Two files will not diff clean, for reasons that are not faults:

- `split_plan.json` records the absolute path of your input PDF.
- `stage1_gate_report.json` records the output directory it ran in.

Everything else is byte-identical across runs, and identical whether you
calibrate fresh or reuse a profile. If anything else differs, something about
your environment differs, and that is worth understanding before you trust a
batch.

Expected gate report for that paper:

```
  questions found                    270
  sections                           9   sizes [30]  (uniform)
  clean 1->N sections                9 / 9
  duplicate q_id                     0
  orphan content pages               0
  rows with a paper label            270 / 270
  advertisement pages removed        70
  of those, colour-flagged           70 (threshold 1.33%)
```

`reference_output/samples/` has three 600 DPI pages. Two of them (187, 387)
are answer pages made entirely of images with no extractable text; they are
there because they are what an over-eager advertisement filter destroys first.
If your run produces those two intact, the classifier is behaving.

## Offline tests

```bash
python test_jeelib.py     # 21 checks: paper-label patterns, colour thresholds
```

No PDF, no model, no cost. Every case is a label this pipeline actually
produced, right or wrong — including `Set D to` and `Set A: For α`, which were
body text on pages 312 and 519 of the 2025 paper and were being emitted as
paper labels.

## What this does and does not produce

Each output PNG is a picture of a **whole page**, images included. The tool
does not extract embedded figures as separate files. If someone asks for one
file per diagram, that is new work, not a flag.

## Known limits

- **Memory.** MuPDF crashes rather than raising when RAM runs short. Stages 2
  and 4 survive this (child processes, escalating low-memory strategies,
  retries, `--resume`); stages 0, 1 and 3 do not, though they use far less
  memory and have not been seen to fail. Under ~1 GB free you will see
  warnings and slower fallback strategies rather than failures. See
  "Crashes under memory pressure" in the README.
- **Render worker recycling was a deadlock, and is fixed.**
  `max_tasks_per_child=20` hangs on Windows when combined with an
  `initializer`: runs stopped dead after exactly `workers × 20` pages, and it
  was misdiagnosed as memory pressure twice. The pool is now rebuilt per batch
  instead. If you ever see a render stall at a round multiple of your worker
  count, that regression is back.
- **A crash-recovered `content.pdf` is smaller** than a clean-path one (5 MB
  vs 19 MB for the same pages) because the fallback strategy does not carry
  the source's orphaned advertisement images. Both are correct.
- **No text layer at all** → the marker logic cannot work and you need OCR
  first. The advertisement detector still works, being purely structural.
- **A new publisher** → re-run stage 0 and read the warnings. If the question
  numbering pattern is not in `QUESTION_PATTERNS`, add it. That is the one
  place human input is genuinely required, and stage 5's 1→N check tells you
  immediately if you got it wrong.
- **Colour is corroboration, never a veto.** It adds +2 to a score that needs
  ≥3, and any page carrying a content marker is immune before scoring starts.
  On 2018 it flagged all 70 advertisement pages and was decisive for none; on
  2025 it fired on nothing, because that document has no advertisements at all.

## Where the reasoning lives

`README.md` explains why each detection rule exists and what breaks without
it, with the measurements behind each threshold. `PROCESS.md` walks the whole
pipeline end to end in prose.

This snapshot ships without a `.git` directory. The repository's history stops
at 14 September and describes none of the work in this version, so it would
have shown every file as uncommitted and every commit message as irrelevant.
Initialise a fresh repository from this snapshot rather than trusting the old
one.
