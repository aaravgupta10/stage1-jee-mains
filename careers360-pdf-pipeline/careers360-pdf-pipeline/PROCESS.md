# How the pipeline works, end to end

A question-paper PDF from Careers360 mixes three things together: the
questions and worked solutions you want, advertisement pages inserted every
few pages, and the structure that says which question belongs to which paper,
shift and subject. That structure is printed on the page but exists nowhere as
data.

This pipeline takes the PDF and produces three things: a clean PDF of content
only, a high-resolution image of every page, and a spreadsheet saying what
every question is. It runs in six stages, each a separate script, chained by
`run_all.py`.

```
  input.pdf (393 pages)
        |
        v
  [ 0  CALIBRATE ]   learn this document's own rules
        |            -> profile.json
        v
  [ 1  CLASSIFY  ]   content page or advertisement?
        |            -> split_plan.json, classification_log.csv
        v
  [ 2  SPLIT     ]   write both halves, keep nothing hidden
        |            -> content.pdf, removed_ads.pdf, page_map.csv
        v
  [ 3  TAG       ]   which paper, shift and subject is each question?
        |            -> questions_index.csv, sections.json
        v
  [ 5  GATE      ]   is this index safe to spend money on?
        |            -> stage1_gate_report.json   HARD STOP on failure
        v
  [ 4  RENDER    ]   600 DPI image of every page
                     -> pages/*.png, removed/*.png
```

The gate is numbered 5 because it was added last, but it runs fourth. Render
is the only stage that costs real time, so nothing reaches it unchecked.

Every file produced carries the **original page number**, so any row in any
output can be traced back to a page in the source PDF.

---

## Stage 0 — Calibrate: learn the document instead of assuming it

**The problem.** The obvious way to write this tool is to hardcode what you
see in the first PDF: questions are numbered `Q. 1`, there are 30 per section,
adverts are the pages with lots of pictures. Every one of those assumptions
breaks on the next paper from the same publisher.

**What it does.** Before touching a single page, stage 0 reads the whole
document and derives its rules from the document itself.

*Question numbering* is discovered by trying ten candidate patterns and
scoring each on whether its matches form a clean 1, 2, 3… progression. A
correct pattern produces an arithmetic sequence; a wrong one produces noise.
This makes the test self-validating — you do not have to know the answer in
advance in order to check it. On the 2018 file:

```
Q. N         hits=270  longest_chain=30  coherence=100%   <- chosen
Q N          hits=  0  longest_chain= 0  coherence=  0%
QN)          hits=  0  longest_chain= 0  coherence=  0%
Question N   hits=  0  longest_chain= 0  coherence=  0%
```

*Thresholds* are measured, not assumed. The publisher's watermark is found by
looking for the image-coverage value that recurs on nearly every
question-bearing page. The "too much vector artwork to be a question" cutoff
is set from this document's own distribution — it came out at 45 on the full
file and 37 on a shorter one.

*Advertisement templates* are found by fingerprinting page layout and looking
for repeats. On the 2018 file, four templates covering 68 pages.

**Output:** `profile.json`, plus a report you read once. For every later paper
from the same publisher, pass `--profile` and this stage is skipped.

**If it is unsure, it says so.** A thin scoring margin produces a warning
rather than a silent guess.

---

## Stage 1 — Classify: which pages are advertisements?

**The problem.** Adverts must go, and the cost of the two possible mistakes is
wildly unequal. Shipping a stray advert is untidy. Deleting a page that held
the answer to question 17 is silent data loss that nobody notices until a
student hits it.

**The insight.** The primary signal is **repetition, not appearance**.
Advertisements exist to be repeated; a genuine question page is never a
near-duplicate of another page. Pages are fingerprinted on layout alone —
image count, rounded image positions, bucketed vector-op count — so the
detector survives a change of advertising campaign, a change of language, or a
missing text layer.

**Why the obvious rule is wrong.** "Mostly images, so it is an advert"
destroys real content:

- page 187 is a genuine answer, the XeOF₄ molecular structure, with **zero
  extractable text** and 87% image coverage
- page 387 is a complete worked trigonometry solution rendered **entirely as
  images**
- real adverts range from 26% to 100% image coverage — the ranges overlap
  completely

Four rules run in order:

1. **Immunity** — any content marker (an option list, "Solution", "Correct
   Answer") means CONTENT, unconditionally. This protects diagram-only pages.
2. **Scoring** — pages with no markers accumulate evidence: repeated template
   +3, hyperlink +2, advert keyword +2, heavy vector artwork +1, watermark
   missing +1, outside the question range +1. Deletion needs **3 points**, so
   at least two independent signals must agree.
3. **Continuation** — a markerless page with no evidence at all is a solution
   spilling over from the previous page, and inherits its verdict.
4. **Fail safe** — anything in between is marked REVIEW, and **REVIEW pages
   are kept**. Shipping a stray page is recoverable; losing an answer is not.

**Result on the 2018 file:** 70 removed, 318 kept outright, 5 flagged for
review and kept. Zero false positives on content pages.

**Output:** `split_plan.json` (the decision) and `classification_log.csv`
(every page, its verdict, the reason, and every raw signal — so any decision
can be audited afterwards).

---

## Stage 2 — Split: write both halves

**What it does.** Writes `content.pdf` (questions and solutions) and
`removed_ads.pdf` (everything stripped out). Both halves are kept, so no
decision made by stage 1 is irreversible and every removal can be inspected.

`page_map.csv` records which original page each page of `content.pdf` came
from. This is the thread that ties every later output back to the source.

**What can go wrong.** The PDF library crashes — not raises, *crashes* — when
the machine runs short of memory. A crash cannot be caught in the normal way;
the program is simply gone. So the actual write happens in a **child process**
that the main script can outlive and retry, and the retries escalate to
progressively lower-memory methods rather than repeating the one that just
died:

| Attempt | Method | Speed | Memory |
|---|---|---|---|
| 1 | `select` — prune the page tree, save once | ~1 s | highest |
| 2 | `runs` — copy page ranges into a new document | slower | much flatter |
| 3 | `runs-nodeflate` — same, compression off | slowest | lowest |

Every attempt is **verified** by reopening the output and counting its pages,
because a process killed mid-write leaves a truncated file that still looks
like a valid PDF and would otherwise flow silently into stage 3.
`page_map.csv` is written only after both halves are confirmed good.

This was tested on a machine with 0.15 GB free: both halves crashed on attempt
1 and both recovered on attempt 2, and the pipeline completed normally.

---

## Stage 3 — Tag: which paper, shift and subject?

**The problem.** A 393-page file holds three separate exam sittings, each with
three subjects — nine sections. Nothing in the PDF's data says where one ends
and the next begins. The information is printed on the page for a human to
read.

**Two independent signals, required to agree:**

- **Printed section headers** — the subject and shift names appear in the text
  where each section starts.
- **Question-number resets** — numbering runs 1 to 30 and restarts at each
  boundary.

Either signal alone is dangerous. Headers alone mean one missed header
silently reassigns an entire block of questions to the wrong subject.
Numbering alone gives you boundaries with no names. **Requiring them to agree
converts a silent wrong answer into a loud failure** — which is the whole
point, because nobody proofreads 270 rows by hand.

**A concrete bug this caught.** Section headers appear *mid-page*: page 41
reads `Q. 30 … Chemistry … Q. 1`. A naive page-by-page pass labelled Physics
question 30 as Chemistry. The fix was to walk the text by character position
rather than by page.

**When it cannot be sure**, it degrades rather than guessing: an unrecognised
subject becomes a positional label (`section_4, pages 77-123`) plus a warning;
continuous numbering with no resets is detected and reported; if neither
signal is available it says so instead of inventing an answer.

**The id must not come from the label.** `q_id` is `S07|Physics|Q011`, built
from the section index. It used to be built from the paper label, and on the
2025 paper that label was a regex misfire: `Set B :` came out for several
different sections at once, so 675 questions collapsed onto 504 ids. Two
different questions became the same id, and because `q_id` is the filename in
the next stage, one silently overwrote the other. The section index is derived
from the numbering resets above, so it is unique by construction no matter how
badly the labels read. The label is still recorded, in its own column, for
reporting.

**Output:** `questions_index.csv` — one row per question with its id, paper,
shift, subject, number and page span — and `sections.json`.

---

## Stage 5 — Gate: is this index worth spending on?

**The problem.** Everything above can succeed and still produce an index that
is quietly wrong. The 2025 run printed a clean nine-column section table, said
`validation: OK`, and carried 171 duplicate ids. The downstream stage would
have written 504 files for 675 questions and reconciled perfectly, because
reconciliation counts records, not files.

**What it does.** Reads only what the earlier stages already wrote — no PDF, no
model, no cost — and answers seven questions: how many questions were found;
how many sections and how big; how many sections number cleanly 1→N; how many
duplicate ids; which content pages sit in no question's span; how many rows
carry a paper label; how many advertisement pages were removed and how many of
those the colour signal flagged.

**Two of them stop the run:** a duplicate id above zero, or a section that is
not a clean 1→N run. Across 2017, 2019, 2021, 2023 and 2025 — 114 sections —
both checks passed every time, so they are cheap and they fire only when
something is genuinely broken.

Everything else is reported and left to a person. Orphan pages are usually
covers. A missing paper label is now only a reporting gap, because the label
stopped being load-bearing when the id changed.

**Output:** `stage1_gate_report.json`, and exit code 1 if a hard stop fired.

---

## Stage 4 — Render: the images

**What it does.** Renders every page to a 600 DPI PNG, named by its original
page number. Both halves are rendered; the removed pages too, so the removals
can be checked by eye.

**Why PNG and not JPEG.** These pages are black line art, equations and text
on white — the worst case for JPEG, which rings around thin strokes and smears
subscripts, and the best case for PNG. Measured on page 3 at 600 DPI: PNG
0.53 MB, JPEG 1.06 MB. PNG is half the size *and* lossless, so there is no
trade-off to weigh here.

**Cost.** About 290 MB and 35 minutes single-threaded for a 393-page file.
Rendering is perfectly parallel, so it drops to a few minutes across cores.

**Memory.** One 600 DPI page held in memory is about 104 MB, and roughly 313
MB at peak. Eight workers is therefore ~2.5 GB, which fails *intermittently* —
it depends on what else the machine happens to be doing. So the pool is sized
against memory that is actually free at that moment, pages are retried,
workers are recycled, and `--resume` skips images that already exist. A failed
page is recorded with its real error in `render_failures.json` rather than
taking the whole run down.

---

## What you end up with

```
out/
  profile.json              the calibrated rules (reuse for the next paper)
  content.pdf               questions + solutions only
  removed_ads.pdf           everything stripped out, for checking
  pages/page_0003.png       600 DPI, named by ORIGINAL page number
  removed/page_0008.png     removed pages, rendered for verification
  classification_log.csv    every page: verdict, reason, raw signals
  split_plan.json           keep / advert / review page lists
  page_map.csv              content.pdf page -> original page
  questions_index.csv       one row per question
  sections.json             sections with page spans and question ranges
  stage1_gate_report.json   the gate's verdict, and why
```

**A full run of the JEE Main 2018 official papers:**

| | |
|---|---|
| Input | 393 pages |
| Content kept | 323 pages |
| Advertisements removed | 70 pages |
| Flagged for review, kept | 5 pages |
| Advertisements the colour signal flagged | 70 of 70 |
| Questions indexed | 270 |
| Sections | 9 (3 papers × 3 subjects) |
| Shifts | Offline 16 Apr, Online 15 Apr AM, Online 15 Apr PM |
| Images | 393 PNGs, 4958 × 7016, 284 MB |
| Time | ~40 s without rendering; ~6 min with, across 8 cores |

---

## The idea running through all of it

Three rules decide almost every design choice here:

**Measure the document, do not assume it.** Every threshold, pattern and
template is derived from the file in front of you. A publisher redesign shifts
the numbers automatically instead of breaking the tool.

**Two weak signals that agree beat one strong signal.** Repetition plus
keywords for adverts; headers plus numbering for sections. The purpose is not
higher accuracy — it is turning silent wrong answers into loud failures.

**Losing content is worse than keeping junk.** Uncertain pages are kept and
flagged, both halves of the split are written, and every artefact can be
traced back to its original page. Every mistake this tool can make is designed
to be visible and reversible.
