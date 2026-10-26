# Progress log — JEE Mains even-years PYQ pipeline run

Purpose of this file: a running record of every plan, action, decision and
pivot on this task, so context is never lost between sessions. Newest entries
at the bottom of each section. Nothing here is deleted — corrections are added
as new entries, not edits to old ones.

## Task

Internship task (Careers360 pipeline). Inputs: JEE Mains "even year" PYQ PDFs
(one shift per year) dropped into this folder by the user. A senior already
built a PDF-processing pipeline (`careers360-pdf-pipeline/`) that splits each
paper into content vs. ads, renders pages at high DPI, and tags every question
with paper/shift/subject into a CSV. Job: run the pipeline on the supplied
PDFs and hand the outputs to the senior.

## Inventory (as found, 2026-09-21)

Root: `C:\Users\Aarav Gupta\Downloads\stage1_jeemains_even_years\`

Pipeline code lives one level deeper than expected — there are two nested
`careers360-pdf-pipeline` folders:
`stage1_jeemains_even_years\careers360-pdf-pipeline\careers360-pdf-pipeline\`
is the one with the actual scripts (`run_all.py`, `jeelib.py`, etc). Noting
this so we don't waste time on the wrong path again.

Input PDFs (page counts via PyMuPDF):

| File | Pages | Size | Treated as |
|---|---|---|---|
| JEE Main 2018 Official Question Papers With Detailed Solutions | 393 | 15.3 MB | real paper — run |
| JEE Main January 2020 Official Question Papers With Detailed Solutions | 544 | 32.4 MB | real paper — run |
| JEE Main 2022 June Session Official Question Papers With Detailed Solutions | 1004 | 62.5 MB | real paper — run |
| JEE MAIN 2024 April Session Official Question Papers With Detailed Solutions | 764 | 44.4 MB | real paper — run |
| JEE Main 2026 January Session Question Paper with Solution | 593 | 9.9 MB | real paper — run |
| JEE Main Previous 10 Year Questions with Detailed Solutions (2017-2026) | 5 | 0.3 MB | **excluded** — 5 pages, clearly a summary/index doc, not a question paper. Confirming contents before final handover. |

All 5 real papers are even years (2018, 2020, 2022, 2024, 2026) — matches
"1 shift from each even year" as described by the user.

`reference_output/` in the pipeline folder holds a verified run of the 2018
PDF specifically — we can diff against it to confirm our environment
reproduces the senior's results before trusting the pipeline on the other 4
files.

## Environment setup

- The sandboxed `python` on PATH (hermes-agent venv) has no pip and is
  missing `fitz`/`numpy` — unusable for this task.
- Found a real Python 3.12.4 install at
  `C:\Users\Aarav Gupta\AppData\Local\Programs\Python\Python312\python.exe`
  with pip and pymupdf/psutil/numpy **already installed**. Using this
  interpreter for every pipeline invocation from here on.
- `python test_jeelib.py` (21 offline checks) — all pass, but only after
  forcing UTF-8 output (`PYTHONUTF8=1 PYTHONIOENCODING=utf-8`); the default
  Windows cp1252 console crashes on a Greek alpha in one test string. Cosmetic
  console issue only, not a pipeline bug. **Always set those two env vars
  when invoking any script in this pipeline on this machine.**
- 16 logical cores available for the render stage's worker pool.

## Plan

1. ~~Explore inputs and pipeline docs (README/HANDOVER/PROCESS).~~ Done.
2. ~~Set up environment (python interpreter, deps, offline tests).~~ Done.
3. Verify our environment reproduces the senior's reference run: run the
   pipeline on the 2018 PDF, diff `questions_index.csv` / `sections.json` /
   `classification_log.csv` against `reference_output/`.
4. For each of the other 4 papers: run `00_calibrate.py` fresh (do NOT reuse
   the 2018 `profiles/careers360.json` profile across years — the README
   explicitly warns this silently corrupts results when the format differs,
   e.g. 2025 vs 2018) and review the calibration report for warnings.
5. Run `run_all.py --skip-render` per paper, then `05_gate_report.py` gate
   check (hard stops: duplicate q_id, any section not a clean 1→N run).
   Fix/investigate anything that fails before spending time on rendering.
6. Once all 5 gate reports are clean, run the full pipeline with rendering
   (600 DPI PNGs) for all 5 papers.
7. Assemble deliverables per paper (content.pdf, questions_index.csv,
   sections.json, gate report, page images) into `output/<year>/` for handoff
   to the senior.
8. Write a short handoff summary.

## Actions taken / findings

- (2026-09-21) Read README.md, HANDOVER.md, PROCESS.md in full. Confirmed
  correct invocation pattern: calibrate once per *format* (not blindly reused
  across years), run_all with --skip-render first, gate before render.
- (2026-09-21) Installed/confirmed pymupdf, psutil, numpy on Python 3.12 —
  see Environment setup above.
- (2026-09-21) Ran `test_jeelib.py` — 21/21 pass (after UTF-8 env fix).
- (2026-09-21) Counted pages on all 6 uploaded PDFs — see Inventory table.
  Flagged the 5-page "Previous 10 Year Questions" file as excluded pending
  content check — it's too short to be a real paper.

## Pivots / decisions (with reasoning)

- Excluding the 5-page "Previous 10 Year Questions" PDF from the main pipeline
  run rather than forcing it through — the pipeline assumes a real
  Careers360 question-paper layout (question markers, sections, ad clusters);
  a 5-page file is almost certainly a promotional/index page, and running the
  full pipeline on it would either fail loudly (fine) or, worse, "succeed"
  with garbage output. Will eyeball its actual content before final handoff
  in case it needs to be handled differently (e.g. just passed through as-is).
- Calibrating fresh per paper year instead of reusing one profile across all
  5 — directly per HANDOVER.md's documented incident where reusing an
  unrelated profile silently broke watermark/ad detection on a
  different-format paper.

- (2026-09-21) User asked whether this needs a GPU. Answered: no — the
  pipeline is PyMuPDF parsing/regex/image-fingerprinting plus PNG rendering,
  all CPU-bound, no ML/OCR inference. 16 cores available will parallelize the
  render stage.
- (2026-09-21) Read the 5 pages of "Previous 10 Year Questions" PDF directly.
  Confirmed: it's a Careers360 promotional/index booklet (cover, intro letter,
  a table of "Click Here" links per year, an ebook/counselling ad page) — not
  a question paper. **Final decision: excluded from the pipeline run.** Will
  mention it to the senior as received-but-not-processed rather than silently
  dropping it.

- (2026-09-21) **Verification run: 2018 PDF, `--skip-render`, output to
  `output/2018_verify/`.** Gate PASSED: 270 questions, 9 sections all uniform
  size 30, 9/9 clean 1->N, 0 duplicate q_id, 270/270 paper labels, 70 ad pages
  removed (70/70 colour-flagged). `diff` against `reference_output/` for
  `questions_index.csv`, `sections.json`, `classification_log.csv` — **all
  three byte-identical, zero diff**. Environment and pipeline fully verified
  to reproduce the senior's known-good run. Safe to proceed to the other 4
  papers.

- (2026-09-21) **2020 January paper**, fresh calibration, `--skip-render`,
  output `output/2020_jan/`. Gate PASSED: 450 questions, 18 sections, all
  uniform size 25, 18/18 clean 1->N, 0 duplicate q_id. Matches the "6 shifts"
  figure for this session from the Careers360 index PDF (18 sections / 3
  subjects = 6 shifts). **Note:** `rows with a paper label: 0/450` — the
  paper/shift-name regex found nothing for this document's header format. Per
  README this is non-fatal and reporting-only (q_id is built from section
  index, not the label), but it means the "paper" column will be **blank**
  for every 2020 row in the deliverable. Flagging for senior rather than
  guessing shift names.
- (2026-09-21) **2022 June paper**, fresh calibration, `--skip-render`,
  output `output/2022_june/`. Gate PASSED: 1080 questions, 36 sections, all
  uniform size 30, 36/36 clean 1->N, 0 duplicate q_id, 1080/1080 rows carry
  *some* paper label. **Data-quality issue found:** sections 19-36 (18 of the
  36 sections, pages ~489-1000) are all labelled `"Online 26th June Evening
  Shift"` in the paper/shift column even though their original page ranges
  are clearly sequential and distinct — the label-extraction regex is getting
  stuck on the last real match instead of finding the next date/shift header.
  This is exactly the class of bug the pipeline's own docs describe ("a
  publisher redesign shifts label text and a naive pass repeats the last
  value") — **the question data itself (subject, q_no, page span, q_id) is
  not affected**, q_id is derived from section_index (numbering resets +
  header cross-check) which is unique and correct by construction; only the
  human-readable "paper" text column is wrong for those 18 sections. Not
  attempting to hand-guess the correct shift dates from memory — will flag
  this explicitly to the senior as a known limitation of this document's
  label format rather than silently shipping wrong labels or fabricating
  corrected ones.

- (2026-09-21) **2024 April paper, first attempt** — `--skip-render`,
  `output/2024_april/`. Calibration succeeded (900 questions, pattern `Q. N`
  100% coherence). **Stage 3 (tag_subjects) hard-stopped** with:
  `header-derived boundaries [...] disagree with numbering resets [...]`.
  Section table showed sections 7 AND 8 both labelled "Physics" back-to-back
  with no Chemistry in between, spanning original pages 143-192.

## Investigation: 2024 April mislabeled Chemistry section

Read the actual PDF pages with PyMuPDF directly (bypassing the pipeline) to
find ground truth. Original page 171 (start of what the pipeline called
section 8) is unambiguously Chemistry content (Dalton's atomic theory, etc.)
— confirmed by dumping its raw text.

Root cause, confirmed by checking exactly where `SUBJECT_HEADER` matches land
on the page (relative character offset): on original page 143 the word
"Physics" sits at ~7% into the page's text (a true header, before Q.1). On
original page 171 the word "Chemistry" sits at ~98% into the page's text —
*after* Q.1's full question, options and solution, immediately before the
page number. Same document, same regex, two different physical placements for
the subject label depending on the section transition.

`jeelib.ordered_events()` is called **once per page** and walks events by
character offset within that page (this is deliberate and correct — it's the
existing fix for 2018's *mid-page* header case, e.g. "Q. 30 … Chemistry …
Q. 1"). But it assumes a header always precedes the questions it applies to.
On page 171 the label follows Q.1 instead of preceding it, so in
`03_tag_subjects.py`'s per-page loop (`for pno, text in page_texts: for kind,
val in ordered_events(...): ...`), the "subject" state variable is still
"Physics" (carried from the previous page) when Q.1's hit is recorded, and
only flips to "Chemistry" afterward — for every subsequent question. Net
effect: exactly one question per affected transition (the first one of a new
section, only when that section's label is footer-style rather than
header-style) is silently mislabeled with the *previous* section's subject.
Because section *naming* was `name = block[0]["subject"]` (literally the
first question in the numbering-reset block), that one mislabeled question
hijacked the name for the whole 30-question block.

This is a real, narrow document-format quirk in the 2024 paper specifically
(2018/2020/2022 don't hit it — all their transitions are header-before-
questions). It is exactly the situation `HANDOVER.md` describes as "the one
place human input is genuinely required" — not a numbering-pattern gap this
time, but a label-placement inconsistency stage 3 wasn't written to expect.

## Decision: patch `03_tag_subjects.py`, not paper over it with `--no-strict`

Chose to fix the code rather than blanket-suppress with `--no-strict`,
because `--no-strict` would also silence *any other, unrelated* validation
failure in the same run without telling us — exactly the "silent wrong
answer" failure mode this pipeline was built to avoid (see stage 5's whole
reason for existing, per README/PROCESS.md). A targeted fix that we can
verify against the known-good 2018 reference is safer than turning off the
checks.

**Safety net first:** `git init`-ed the pipeline folder and committed the
exact snapshot as received from the senior (commit `89891b6`, message
"Snapshot of pipeline as received from senior (pre-fix)") before changing
anything, so every subsequent edit is diffable and revertible. `HANDOVER.md`
itself says the snapshot ships without git history and recommends
initializing fresh — did that rather than editing untracked.

**The fix**, in `03_tag_subjects.py` (`careers360-pdf-pipeline/careers360-pdf-pipeline/`):
1. Section subject/paper name is now the **majority vote** across all
   questions in the numbering-reset block (`collections.Counter`), not
   `block[0]`'s value alone. For every already-clean document (2018 etc.)
   this is mathematically identical to the old behaviour, since 100% of a
   block already agrees — confirmed no change by re-diffing 2018 against
   `reference_output/` (byte-identical, twice, after each edit round).
2. Per-question `paper`/`subject_label` fields are homogenized to the
   section's resolved majority value (previously only `subject_label` was
   homogenized; `paper` on each row still carried the raw, occasionally-stale
   per-question value).
3. Replaced the old blanket check (`if reset_bounds != label_bounds:
   problems.append(...)` → always fatal) with a precise per-section check:
   if the non-majority ("stray") rows are a small minority (≤10% of the
   block, or a single row for tiny blocks), it's logged as an **auto-
   corrected note** (non-fatal) explaining exactly what was stale and what
   was substituted; if there's no clear majority at all, it's still a
   **fatal problem** exactly as before — genuine ambiguity still stops the
   run loudly, only the narrow label-timing artifact is now recovered
   automatically.
4. Split the exit logic into `problems` (fatal, as before) vs `notes`
   (informational, printed but non-fatal) so a run can now report
   "validation: OK, with the auto-corrections noted above" instead of hard
   stopping on something it already resolved correctly.

Verified: offline `test_jeelib.py` still 21/21 pass after the edit; 2018 full
pipeline re-run twice (once per edit round) and diffed byte-identical against
`reference_output/` both times.

- (2026-09-21) **2024 April paper, re-run with the fix**, `--skip-render`,
  output `output/2024_april/`. Gate PASSED: 900 questions, 30 sections all
  uniform size 30, 30/30 clean 1->N, 0 duplicate q_id. Confirmed via
  `sections.json` that all 30 sections now correctly cycle
  Physics -> Chemistry -> Mathematics with no repeats (section 8, pages
  171-192, is now correctly "Chemistry" — previously "Physics"). Stage 3
  exited 0 (non-fatal note only, no hard stop), as designed. `rows with a
  paper label: 0/900` — same blank-shift-label situation as 2020, noted for
  the senior. `orphan_content_pages: [1]` — page 1 is the cover, expected.

- (2026-09-21) **2026 January paper**, fresh calibration, `--skip-render`,
  output `output/2026_jan/`. Gate PASSED cleanly, first try, no code changes
  needed: 750 questions, 30 sections uniform size 25, 30/30 clean 1->N, 0
  duplicate q_id, `validation: OK` (signals agree). Same blank-paper-label
  situation as 2020/2024 (0/750) — consistent with those two, noted once for
  all three below rather than repeating per-file.

## All 5 real papers now gate-passed

| Paper | Questions | Sections | Section size | Gate | Paper/shift label |
|---|---|---|---|---|---|
| 2018 | 270 | 9 | 30 | PASS (byte-identical to senior's reference) | 270/270 present |
| 2020 Jan | 450 | 18 | 25 | PASS | 0/450 — blank (regex found nothing) |
| 2022 June | 1080 | 36 | 30 | PASS | 1080/1080 present, but **18 of 36 sections carry the wrong shift-date text** (all say "Online 26th June Evening Shift" — see earlier entry). Subject/q_no/page data unaffected. |
| 2024 April | 900 | 30 | 30 | PASS (after our stage-3 fix) | 0/900 — blank |
| 2026 Jan | 750 | 30 | 25 | PASS, no code changes needed | 0/750 — blank |

Total: 3450 questions indexed, 3298 content+ad pages across the 5 papers.

- (2026-09-21) Cleaned up `output/` — kept one canonical `output/2018/` (was
  `2018_verify_v3`; deleted the earlier redundant `2018_verify` copy).
  Final canonical output dirs: `2018/`, `2020_jan/`, `2022_june/`,
  `2024_april/`, `2026_jan/`.
- (2026-09-21) **Started the render stage** (600 DPI PNGs, the only
  expensive stage) for all 5 papers, sequentially, in the background —
  `output/render_all.log`. Ran `04_render.py -o <dir> --dpi 600 --resume`
  per paper directly (not `run_all.py`) since stages 0-3+5 already completed
  and are gate-passed in each `output/<dir>/` from the earlier dry runs;
  `--resume` means a partial/interrupted render can be restarted for free.
  Sequential, not parallel across papers — only ~4.3 GB free RAM on this
  machine (16.8 GB total), and running 5 render pools at once would fight
  over that budget in ways the pipeline's per-process memory cap can't see
  across processes. The pipeline's own worker cap sizes each run's pool
  against real free memory automatically. Total ~3298 content+ad pages
  across all 5 papers to render; rough order-of-magnitude estimate a few
  tens of minutes to ~1-2 hours depending on how many workers the memory
  budget allows.

- (2026-09-21) **Render stage complete for all 5 papers, zero failures.**
  `output/render_all.log` shows every paper finishing `all pages rendered,
  no failures`, worker counts auto-capped by real free memory (6-12 workers
  depending on what else was running on the machine at the time). Verified
  file counts in each `output/<dir>/pages/` and `.../removed/` match the
  gate report's content/ad page counts exactly:

  | Paper | pages/ (content, 600 DPI) | removed/ (stripped ads, for audit) |
  |---|---|---|
  | 2018 | 323 | 70 |
  | 2020_jan | 543 | 1 |
  | 2022_june | 908 | 96 |
  | 2024_april | 763 | 1 |
  | 2026_jan | 582 | 11 |

  Total `output/` size: 2.8 GB. Each `output/<paper>/` now has the full
  documented deliverable set: `content.pdf`, `removed_ads.pdf`, `pages/*.png`,
  `removed/*.png`, `questions_index.csv`, `sections.json`,
  `classification_log.csv`, `split_plan.json`, `page_map.csv`, `profile.json`,
  `stage1_gate_report.json`.

- (2026-09-21) Spot-checked rendered output quality: opened
  `output/2018/pages/page_0187.png` directly — this is the pipeline docs' own
  hardest test case (the XeOF₄ structure page: zero extractable text, a naive
  "mostly image = ad" filter would delete it). Renders clean, sharp, full
  4958x7016 resolution, correctly kept as content. Confirms the ad classifier
  and render stage are both working correctly, not just "exiting 0."

## Status: DONE — all 5 papers processed end-to-end (calibrate -> classify -> split -> tag -> gate -> render), all gates passed, zero render failures, output quality spot-checked. Task complete pending user review.

---

# HANDOFF SUMMARY (for the senior)

**What was run:** all 5 even-year JEE Main papers supplied (2018, 2020 Jan,
2022 June, 2024 April, 2026 Jan) through the full pipeline —
calibrate -> classify -> split -> tag -> gate -> render (600 DPI). All 5
passed the stage-5 gate (0 duplicate q_ids, every section a clean 1->N run)
and rendered with zero failures. Deliverables are in
`output/<paper>/` (see file list below), 2.8 GB total.

**Not processed:** `JEE Main Previous 10 Year Questions with Detailed
Solutions (2017-2026)...pdf` — read its 5 pages directly; it's a Careers360
promotional/index booklet (cover, intro letter, a table of "Click Here"
download links per year, an ebook ad), not a question paper. Left out of the
pipeline run rather than forced through.

**One code fix was needed and made**, in
`careers360-pdf-pipeline/careers360-pdf-pipeline/03_tag_subjects.py`: on the
2024 April paper, stage 3 correctly hard-stopped because a subject-boundary
cross-check disagreed. Traced it to a genuine document quirk — this paper
sometimes prints a section's subject name *after* its first question
(as a footer under the solution) instead of before it as a header, which is
what every other paper here does and what the original code assumed. That
made the first question of each affected section inherit the *previous*
section's subject name, which then also hijacked the whole section's label
(section 8, pages 171-192, was coming out "Physics" when it's unambiguously
Chemistry — verified against the raw PDF text). Fixed by naming each section
from the majority subject across its questions instead of trusting only the
first one, and made the validation check precise enough to auto-correct that
one-row artifact as a logged, non-fatal note while still hard-stopping on any
*real* disagreement (no clear majority). Verified safe: re-diffed the 2018
output against the senior's own `reference_output/` twice (before and after
the fix) — byte-identical both times — and all 21 offline `test_jeelib.py`
checks still pass. Git history of the change is in the pipeline folder
(`git init`-ed fresh, since the delivered snapshot had none, per
`HANDOVER.md`'s own suggestion): commit `89891b6` is the untouched snapshot
as received, `9ffaf75` is the fix, with a full explanation in the commit
message.

**Known data-quality caveats to be aware of** (none of these are the
question/subject/page-span data being wrong — only the human-readable
"paper/shift" text column):
- **2020 Jan, 2024 April, 2026 Jan:** the paper/shift-name column is blank
  for every row (0/450, 0/900, 0/750). The label-extraction regex found
  nothing in these documents' header format. Non-fatal by design (`q_id`
  doesn't depend on it), but if shift names are wanted for these three,
  that's follow-up work, not something this run produced.
- **2022 June:** paper/shift labels ARE present for all 1080 rows, but 18 of
  the 36 sections (roughly the back half of the document, originally pages
  ~489-1000) are all mislabeled `"Online 26th June Evening Shift"` even
  though they're clearly later, distinct shifts — the label regex got stuck
  repeating its last real match. Did not attempt to hand-correct these from
  memory of the actual 2022 exam calendar; flagging instead of guessing.

**Deliverables per paper**, in `output/<paper>/`:
`content.pdf`, `removed_ads.pdf`, `pages/*.png` (600 DPI, content),
`removed/*.png` (600 DPI, stripped pages, for audit), `questions_index.csv`,
`sections.json`, `classification_log.csv`, `split_plan.json`, `page_map.csv`,
`profile.json`, `stage1_gate_report.json`.

| Paper | Questions | Sections | Content pages | Ad pages removed |
|---|---|---|---|---|
| 2018 | 270 | 9 | 323 | 70 |
| 2020 Jan | 450 | 18 | 543 | 1 |
| 2022 June | 1080 | 36 | 908 | 96 |
| 2024 April | 900 | 30 | 763 | 1 |
| 2026 Jan | 750 | 30 | 582 | 11 |
| **Total** | **3450** | **123** | **3119** | **179** |

## Handoff zip built for the senior

- (2026-09-21) User asked for a single zip with just `content.pdf`,
  `removed_ads.pdf`, `questions_index.csv`, `page_map.csv` per paper (not
  the full `output/` — no page images, no profile/classification/gate-report
  audit files) to send to the senior directly.
- Staged those 4 files per paper into a temporary
  `deliverables_for_senior/<paper>/` tree, zipped it with Python's
  `shutil.make_archive`, verified with `zipfile.testzip()` (no corrupt
  entries) and a full name listing (20 files across 5 per-year folders, as
  expected), then deleted the temporary staging folder.
- **Result:** `jee_mains_even_years_deliverables.zip` (≈301 MB) at the
  project root, structure:
  ```
  2018/{content.pdf, removed_ads.pdf, questions_index.csv, page_map.csv}
  2020_jan/{...}
  2022_june/{...}
  2024_april/{...}
  2026_jan/{...}
  ```
- Did not include the 600 DPI page images (`pages/`, `removed/`) — user's
  request named exactly 4 file types, and images are the bulk of `output/`'s
  2.8 GB; if the senior wants those too, that's a separate, much bigger zip
  (or the raw `output/` folder), not implied by this request.

## Corrected paper/shift labels applied (2026-09-24)

User supplied a `new_index_csvs/` folder (`questions_index_2018.csv`,
`_2020jan.csv`, `_2022.csv`, `_2024.csv`, `_2026.csv`) containing corrected
question-index CSVs and asked to swap them into each paper's output folder,
updating anything else that depends on them.

**Verification before touching anything:** compared every new file against
its existing `output/<paper>/questions_index.csv` column-by-column (script,
not eyeballing). Row counts identical (270/450/1080/900/750). `q_id`,
`section_index`, `subject_label`, `q_no`, `content_page`, `page_end`,
`original_page`, `orig_page_end` — **zero mismatches across all 3450 rows,
all 5 papers.** The *only* column that changed anywhere is `paper`:

| Paper | rows with `paper` changed | what changed |
|---|---|---|
| 2018 | 0 | already correct, untouched |
| 2020_jan | 450 / 450 | was blank everywhere -> now real shift labels ("7th January Shift-1", etc., 6 shifts) |
| 2022_june | 540 / 1080 (exactly 18 of 36 sections) | **this is precisely the mislabeling bug flagged earlier** — sections 19-36 were all stuck on "Online 26th June Evening Shift"; now correctly read 27/28/29 June morning/evening, 12 distinct real shifts total |
| 2024_april | 900 / 900 | was blank everywhere -> now real shift labels ("4 Apr - Morning Shift", etc., 10 shifts) |
| 2026_jan | 0 | still blank — no correction supplied, left as-is rather than guessed |

Also verified `paper` is uniform within every section_index in the new files
(no internal inconsistency) before treating it as a per-section value.

**Since only the reporting-only `paper` text column changed, and it is
provably NOT load-bearing** (q_id/section_index/pages are exactly what they
were — the same conclusion the pipeline's own docs draw about this column),
`content.pdf`, `removed_ads.pdf`, `page_map.csv`, `pages/*.png`,
`removed/*.png`, `classification_log.csv`, `split_plan.json` and
`profile.json` did **not** need to change and were left untouched.

**What was changed, per paper, in `output/<paper>/`:**
1. Backed up the 3 files about to change (`questions_index.csv`,
   `sections.json`, `stage1_gate_report.json`) to
   `_backup_before_paper_label_fix/<paper>/` at the project root, before
   editing anything.
2. Replaced `questions_index.csv` with the corresponding new file.
3. Patched `sections.json`'s per-section `"paper"` field to match (derived
   straight from the new CSV's now-uniform-per-section value, not
   hand-typed).
4. Re-ran `05_gate_report.py -o output/<paper>` to regenerate
   `stage1_gate_report.json` from the corrected files (this script only
   reads `questions_index.csv`/`sections.json`/`classification_log.csv` —
   no PDF, no re-render, cheap and safe). All 5 still gate **PASS**, 0
   duplicate q_id, all sections clean 1->N (unchanged from before, as
   expected). `rows with a paper label` now correctly reads 270/270,
   450/450, 1080/1080, 900/900 (2026 still 0/750, honestly).
5. Rebuilt `jee_mains_even_years_deliverables.zip` with the corrected
   `questions_index.csv` files (it's one of the 4 files in that zip);
   re-verified with `zipfile.testzip()`, no corrupt entries, 25 entries as
   before.

**Safety net:** nothing was deleted — the pre-correction versions of the 3
changed files per paper are in `_backup_before_paper_label_fix/` at the
project root if anything needs to be compared back or reverted.

## Status: DONE (updated) — corrected paper/shift labels applied to all 5 papers' questions_index.csv + sections.json, gate reports regenerated and still passing, handoff zip rebuilt with corrected data. Backups of the pre-correction files kept at `_backup_before_paper_label_fix/`.
