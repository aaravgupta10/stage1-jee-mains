# Careers360 question-paper PDF pipeline (v2)

Splits question-paper PDFs into content and advertisement halves, renders the
content at high DPI, and tags every question with its paper/shift and subject.

v2 removes the assumptions that broke across papers from the same publisher.
Nothing about question count, shift count, page count or ad artwork is
hardcoded — it is calibrated from each document.

## Install

```bash
pip install pymupdf psutil
```

No OCR needed; these PDFs carry a real text layer.

`pymupdf` is the only hard requirement. `psutil` is optional but strongly
recommended: it is the only way the pipeline can read free memory on Windows
and macOS. Without it `free_memory_gb()` (stage 2) and `available_memory()`
(stage 4) fall back to `/proc/meminfo` and `os.sysconf`, both Linux-only, and
return `None`. The render worker cap then silently disables itself, and stage 2
loses its low-memory warning — on the very platform where the crashes in
"Crashes under memory pressure" below were actually observed. The run still
says which path it took:

```
rendering 323 content pages at 600 DPI (png, 6 workers; 313 MB peak per worker, 1.9 GB usable -> up to 6)
rendering 323 content pages at 600 DPI (png, 8 workers; memory unknown on this platform; no cap applied)
```

The second line means psutil is missing. Install it, or pass
`--memory-budget-gb` to set the budget by hand.

## Run

```bash
# first paper of a new format: calibrate, review the report, then run
python 00_calibrate.py input.pdf -o out/
python run_all.py input.pdf -o out/ --dpi 600

# every paper after that: reuse the profile you already reviewed
python run_all.py next_paper.pdf -o out2/ --profile out/profile.json --dpi 600

# while checking output, skip the expensive render
python run_all.py input.pdf -o out/ --skip-render
```

### Batch

```bash
for f in papers/*.pdf; do
  python run_all.py "$f" -o "out/$(basename "$f" .pdf)" \
      --profile profiles/careers360.json --dpi 600 --workers 8 --no-strict
done
```

`--no-strict` turns validation failures into warnings so one bad file doesn't
stop the batch. Grep the logs for `VALIDATION PROBLEMS` afterwards.

## What varies safely

Verified by running the pipeline against three shapes of the same document
with no reconfiguration between runs:

| Input | Questions | Sections | Shifts | Section sizes | Result |
|---|---|---|---|---|---|
| Full 2018 file (393 pg) | 270 | 9 | 3 | `[30]` | OK |
| One paper only (123 pg) | 90 | 3 | 1 | `[30]` | OK |
| Truncated mid-section (100 pg) | 77 | 3 | 1 | `[17, 30]` | OK |

The third case matters most: unequal section sizes are reported as a fact, not
treated as an error. v1's `--expect-per-section 30` would have aborted.

Thresholds recalibrate per document — the vector-op cutoff came out at 45 on
the full file and 37 on the shorter variants, derived from each one's own
distribution rather than a constant.

## Stage 0 — calibration

`00_calibrate.py` derives everything and prints a confidence report before any
page is touched. On the 2018 file:

```
question numbering
  Q. N         hits=  270 longest_chain=  30 coherence=  100% score=  30.0 <- chosen
  Q N          hits=    0 longest_chain=   0 coherence=    0% score=   0.0
  regime: resets_per_section  (270 questions, 8 resets)

thresholds calibrated from this document
  watermark band   [0.52, 0.68]  (frac 0.6 on 100% of content pages)
  draw_ops_high    45  (content median 15, p99 34)

repeated advertisement templates
  b102a603a7  18 pages
  c09a104647  17 pages
  7ed3c8ec6e  17 pages
  b4b4ae1a36  16 pages
  total pages in ad clusters: 68
```

**Question pattern discovery.** Ten candidate numbering patterns are scored by
whether their hits form monotonic +1 chains. A correct pattern produces an
arithmetic progression; a wrong one produces noise, so the test is
self-validating. The correct pattern scored 100% coherence and every wrong
candidate scored 0%. If the margin is thin, calibration says so in the
warnings rather than picking silently.

**Threshold calibration.** The watermark band is found by locating the image
coverage value that recurs on nearly every question-bearing page — whatever
that value happens to be — and is disabled if no image covers at least 70% of
content pages. The vector-op cutoff is the 99th percentile of content pages
plus 20%, so a publisher redesign shifts it automatically.

Review the report once per format, then reuse `profile.json` forever with
`--profile`.

## Advertisement detection

**Primary signal is repetition, not keywords.** Ads exist to be repeated; a
real question is never a near-duplicate of another page. Pages are
fingerprinted by layout alone — image count, coarsely rounded image positions,
bucketed vector-op count — which ignores text entirely and so survives a
change of campaign, language, or a missing text layer.

On the 2018 file, 4 clusters captured 68 of 69 ads with **zero false positives
on content pages**. The one miss was the one-off cover, picked up by the
secondary signals. This is the fix for the fact that the keyword list rots
every time the publisher changes campaign.

Why image coverage is useless as a discriminator here:

- Page 187 is a genuine answer (a XeOF₄ structure) with **zero extractable
  text** and 87% image coverage.
- Page 387 is a complete worked trigonometry solution rendered **entirely as
  images**.
- Real ads span 26% to 100% coverage.

Any "mostly image → drop it" rule deletes real answers.

Four rules, in order (`jeelib.classify_pages`):

1. **Immunity** — any content marker → CONTENT, unconditionally. Protects
   diagram-only and image-only questions.
2. **Scoring** — markerless pages accumulate evidence: repeated template +3,
   hyperlink +2, ad keyword +2, **colour +2**, high vector-op count +1, missing
   watermark +1, outside the question range +1. Deletion needs ≥3, so at least
   two independent signals must agree.
3. **Continuation** — markerless with no evidence at all is a spillover
   solution and inherits the previous page's verdict.
4. **Fail safe** — anything between is `REVIEW`, and REVIEW is **kept**.
   Shipping a stray page is recoverable; losing an answer is not.

Result on the 2018 file: 70 removed, 323 kept, 5 flagged for review and kept
(15, 291, 315, 387, 389). The one page v2 removes that v1 kept is page 2, the
table of contents — correctly, since it isn't content.

### Colour, the secondary signal

Measured at 50 DPI, counting pixels where `max(R,G,B) − min(R,G,B) > 40`:

| | coloured pixels |
|---|---|
| 14 advertisement pages (2018) | median **29.63%**, range 1.84% – 42.52% |
| 14 content pages (2018) | median **0.06%**, range 0.00% – 0.27% |

Complete separation: the least colourful ad is about seven times more coloured
than the most colourful content page.

The baseline is calibrated **per document**, not hardcoded. `00_calibrate.py`
samples 40 content pages evenly across the file and stores the 95th percentile
as `content_colour_p95`; a page scores above
`max(p95 × 10, 1%)`. The 1% floor exists because a clean document's p95 is
legitimately 0.0000, and ten times zero is zero — which would flag every page
carrying a single coloured pixel. 1% sits in the middle of the measured gap.

**Colour is never a veto.** It adds +2 to a score that needs ≥3, and rule 1
already protects any page carrying a content marker. A content page with a
large coloured figure exists even though none appeared in this sample, and must
not be deletable on colour alone.

What it is for: insurance against a paper whose advertisement wording differs
from the phrases we know. The 2018 document already had two distinct ad
templates and a keyword detector requiring two promotional phrases missed one
of them entirely.

Measured after the change:

- **2018** — 70 ads removed, **all 70 colour-flagged**, colour decisive for 0
  (repetition had already caught them). Exactly the corroboration it is meant
  to be.
- **2025** — 1 ad removed (a table of contents), 0 colour-flagged, threshold
  6.35%. This document genuinely has no advertisement pages, and calibration
  says so in a warning: *no repeated page templates found*.

## Subject tagging

Two independent signals, required to agree:

- **Printed section headers** — subject and shift names are in the text layer
  where each section starts.
- **Question-number resets** — numbering runs 1..N and resets at each boundary.

Headers alone mean one missed header silently reassigns a whole block.
Numbering alone gives boundaries but no names. Requiring agreement converts a
silent wrong label into a loud failure.

This cross-check earned its keep in development: section headers appear
*mid-page* (page 41 reads `Q. 30 … Chemistry … Q. 1`), so a naive per-page
pass labelled Physics Q30 as Chemistry. Events are walked by character offset
instead — `jeelib.ordered_events`.

Graceful degradation:

- **Unrecognised subject** → positional label (`section_4, pages 77-123`) plus
  a warning, not a failure. Relabel a handful of rows by hand.
- **Continuous numbering** (1..1000, no resets) → detected, and boundaries
  fall back to label changes. If neither signal is available the tagger says
  so explicitly instead of guessing.
- **Optional guards** — `--expect-per-section` and `--expect-sections` exist
  for when you do know the shape and want the extra check.

To add an exam family, extend `SUBJECT_CANON` in `jeelib.py`. It already
covers JEE Paper 1 and 2, NEET and BITSAT vocabulary (Physics, Chemistry,
Mathematics, Biology, Botany, Zoology, Aptitude, Drawing, Planning, Reasoning,
English).

### `q_id` comes from the section index, not the paper label

```
S07|Physics|Q011
```

The paper label is produced by a regex over the text layer and is only as good
as that regex. On the 2025 paper it emitted fragments like `Set B :` for several
different sections at once, so 675 questions collapsed onto 504 ids and two
different questions both became `Set_B_:|Mathematics|Q005`. `q_id` is the
filename in stage 2, so those questions overwrite each other in silence, and
stage 2's reconciliation cannot see it because it counts records, not files.

`section_index` is derived from numbering resets cross-checked against headers.
It is sequential and unique by construction, so it cannot collide however bad
the labels are. The paper label is still written to its own column for
reporting; it is simply no longer load-bearing.

The patterns that produce that label use `[ \t]`, never `\s`. `\s` matches a
newline, so inside `^...$` with `MULTILINE` the old patterns ran past the end of
the line and captured body text: 2021 found 0 labels in 540 rows, 2023 0 in
1,080, and 2025 found 322 in 675 of which most were prose. A pattern also
captures the label and nothing else — a line reading `Set D to be the set of`
is not a paper label.

## Stage 5 — gate the index before anything expensive

```bash
python 05_gate_report.py -o out/
```

`run_all.py` runs this **before** the render, because rendering is the 35-minute
stage and there is no point spending it on an index stage 2 will lose questions
from.

It reads only what the earlier stages wrote — no PDF, no model, no cost — and
reports: questions found; sections and questions per section; sections whose
numbering is a clean 1→N run; duplicate `q_id` count; orphan content pages;
rows with a paper label; advertisement pages removed and how many were
colour-flagged.

**Two hard stops:** any duplicate `q_id`, or any section that is not a clean
1→N run. Everything else is reported for a human. Across 2017, 2019, 2021, 2023
and 2025, 114 of 114 sections were clean, so these fire only when something is
genuinely broken — and the 2025 run that lost 171 questions still printed a
tidy section table and reported success.

## PNG, and what 600 DPI costs

Measured on page 3:

| | Dimensions | PNG | JPEG q95 | Render |
|---|---|---|---|---|
| 300 DPI | 2479 × 3508 | 0.23 MB | 0.37 MB | 1.5 s |
| 600 DPI | 4958 × 7016 | **0.53 MB** | 1.06 MB | 6.8 s |

PNG is half the size *and* lossless. These pages are line art and text on
white — the worst case for JPEG's DCT, which rings around thin strokes and
smears subscripts, and the best case for PNG's lossless filtering. No
trade-off to weigh; `--format jpg` exists only for pipelines that require it.

Budget ~170 MB and ~35 minutes single-threaded per 324 pages at 600 DPI.
Rendering is CPU-bound and perfectly parallel, so more workers bring it to a
few minutes. Spot-check first:

```bash
python 04_render.py -o out/ --dpi 600 --pages 3,8,187,387
```

### Render failures

A worker never raises. PyMuPDF exceptions wrap SWIG objects, which cannot be
pickled back to the parent, so raising across the process boundary replaces
the real error with `cannot pickle 'swig_runtime_data5.SwigPyObject'` and
destroys the diagnosis. Workers catch everything, format the traceback to a
string in the child, and return it as data. Failures are collected, written to
`render_failures.json` with full tracebacks, and the first one is printed.

Memory is the usual cause. A 600 DPI A4 pixmap is ~104 MB raw and ~313 MB at
peak, because PyMuPDF holds the samples buffer, the compression output and the
open document at once. Eight workers is roughly 2.5 GB, which fails
intermittently rather than reproducibly — it depends on what else the machine
is doing when the run starts.

Mitigations, all on by default:

- **Worker cap from real free memory.** The pool is sized against memory that
  is actually free right now, not a guessed constant — via `psutil` where it
  is installed, `/proc/meminfo` on Linux without it. On a busy 8 GB machine
  `--workers 8` at 600 DPI drops to 1 and says so; on an idle 32 GB machine it
  stays at 8. `--no-worker-cap` overrides, and `--memory-budget-gb` sets the
  budget by hand when neither source is available.
- **Retry.** Each page gets `--attempts 2` before being recorded as failed,
  which absorbs transient pressure.
- **Explicit free.** Pixmaps are released in a `finally` block instead of
  waiting for the collector.
- **Worker recycling, in batches.** Jobs run in batches of
  `workers × TASKS_PER_WORKER` (20) and the whole pool is torn down and rebuilt
  between batches, so no process accumulates fragmentation across hundreds of
  100 MB allocations.

  This used to be `max_tasks_per_child=20`, which **deadlocks**: combined with
  an `initializer` on Windows the pool hangs once it retires its first
  generation of workers, and runs stopped dead after exactly `workers × 20`
  pages — 100, then 180, then 260 at different worker counts. It was
  misdiagnosed as memory pressure twice, because a hang after N pages looks
  exactly like one. Rebuilding the pool per batch frees every worker's memory
  at a known point and does not depend on the recycling machinery at all.
- **Resume.** `--resume` skips pages whose image already exists, so a partial
  run costs only the missing pages.

```bash
python 04_render.py -o out/ --dpi 600 --resume          # retry what's missing
python 04_render.py -o out/ --dpi 600 --workers 2       # if it keeps failing
```

The script exits non-zero when any page failed, so a batch loop can detect it.

## Crashes under memory pressure

MuPDF can **segfault**, not raise — Windows `0xC0000005` access violation,
POSIX SIGSEGV. A segfault cannot be caught in-process: there is no exception
to trap, the interpreter is simply gone. Both stages that call MuPDF hard are
protected, but by different means, because stage 4 can catch its errors and
stage 2 cannot.

Observed on a machine at 94% RAM with 0.45 GB free: stage 2 failed 3 of 5
standalone attempts and 6 of 6 through `run_all.py`. It is not
content-specific — the same page fails and then passes minutes later. On a
machine with a few GB free, none of this fires.

### Stage 2 (split)

The write runs in a **child process** the parent can outlive, watch and retry.
Attempts escalate to progressively lower-memory strategies rather than
repeating the allocation that just died:

| Attempt | Strategy | Approach | Trade-off |
|---|---|---|---|
| 1 | `select` | prune the page tree, save whole | ~1 s, highest peak memory |
| 2 | `runs` | copy contiguous page runs into a fresh document | ~70 small allocations instead of one large one |
| 3 | `runs-nodeflate` | same, compression off | biggest file, lowest memory |

All three produce identical page counts (verified: 323 content, 70 ads), and
stage 3 gives identical results from any of them.

Every attempt is **verified** by reopening the output and checking its page
count. A process killed mid-write leaves a plausible-looking truncated PDF
that would otherwise flow silently into stage 3. Partial files are deleted so
`--resume` cannot mistake one for a real output, and `page_map.csv` is written
only once both PDFs verify, so a failed run never leaves a mapping pointing at
a file that isn't there.

```bash
python 02_split.py input.pdf -o out/ --resume                  # keep good outputs
python 02_split.py input.pdf -o out/ --strategy runs-nodeflate # force lowest memory
```

### Stage 4 (render)

Catches in the child and returns the traceback as a string — see the render
section above.

### Exit codes

`run_all.py` decodes exit codes instead of printing the raw number, and
distinguishes a **crash** from an **error**:

```
02_split.py FAILED — 0xC0000005 access violation (Windows) — a native crash
inside MuPDF, almost always memory pressure

This was a CRASH, not an error. The process was killed outright, so no
failure report was written and nothing was logged by the stage itself.
The cause is almost certainly memory. Free some RAM, then:
  python 02_split.py INPUT.pdf -o OUTDIR --resume
  python 02_split.py INPUT.pdf -o OUTDIR --strategy runs-nodeflate
```

The distinction drives the advice. A crash means memory and retrying helps. A
clean non-zero exit means a Python exception — a bad file or a bad plan — and
retrying will not help, so the script says so and points at the tracebacks
instead of sending you to free RAM for no reason.

Decoded: `3221225477` / `3221225725` / `3221226505` (Windows), `-11` / `-9` /
`-6` (POSIX signals), `137` / `139` (shell-reported signals).

## Output

```
out/
├── profile.json                calibrated detection profile (reusable)
├── content.pdf                 questions + solutions only
├── removed_ads.pdf             everything stripped out
├── pages/page_0003.png         600 DPI, named by ORIGINAL page number
├── removed/page_0008.png       stripped pages, rendered for verification
├── classification_log.csv      every page: verdict, reason, all raw signals
├── split_plan.json             keep / ad / review page lists
├── page_map.csv                content.pdf page -> original page
├── questions_index.csv         q_id, paper, subject, q_no, page spans
├── sections.json               sections with page spans and Q ranges
├── split_failures.json         written only if stage 2 could not write a half
└── render_failures.json        written only if stage 4 lost pages
```

Every artefact joins on the original page number.

## Notes and limits

- `02_split.py` prefers `Document.select()` (0.4 s). A blank document plus one
  `insert_pdf()` per page rebuilds the xref on every call and takes over five
  minutes on a 393-page file.
- `select()` leaves orphaned ad images behind, so content.pdf is no smaller
  than the source. `--compress` reclaims that (~90 s to save ~2 MB).
- **No text layer at all** → the marker logic cannot work and you need OCR
  first. The repetition-based ad detector still works, being purely
  structural.
- **A genuinely new publisher** → re-run stage 0 and read the warnings. If the
  question pattern isn't in `QUESTION_PATTERNS`, add it; that is the one place
  human input is genuinely required.
