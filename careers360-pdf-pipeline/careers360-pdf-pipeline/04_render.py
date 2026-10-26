#!/usr/bin/env python3
"""
Stage 4 — render pages to high-resolution images.

Filenames carry the ORIGINAL page number, not the content.pdf page number, so
an image can always be traced back to the source document.

Format: PNG is the default, and on this document it is both smaller and
better than JPEG. Measured on page 3 at 600 DPI: PNG 0.53 MB, JPEG q95
1.06 MB. Pages are black line art, equations and text on white — the worst
case for JPEG's DCT (it rings around thin strokes and smears subscripts) and
the best case for PNG's lossless filtering. --format jpg exists only for
pipelines that require it.

Failure handling: a worker NEVER raises. PyMuPDF exceptions wrap SWIG
objects, which cannot be pickled back to the parent process, so raising
across the process boundary replaces the real error with
"cannot pickle 'swig_runtime_data5.SwigPyObject'" and destroys the
diagnosis. Workers therefore catch everything, format the traceback to a
plain string in the child, and return it as data. See _render().

Memory: a 600 DPI A4 RGB pixmap is about 104 MB. Eight workers holding one
each is ~840 MB before Python overhead, which is what makes high worker
counts fail intermittently rather than reproducibly. Pixmaps are freed
explicitly, workers are recycled, and --workers is capped against a memory
budget unless you override it.

    python 04_render.py -o out/ --dpi 600
    python 04_render.py -o out/ --dpi 600 --pages 3,8,187,387   # spot check
    python 04_render.py -o out/ --dpi 600 --resume              # skip finished
"""

import argparse
import csv
import json
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pymupdf

_SRC: str | None = None

# A worker's peak is well above the raw pixmap: PyMuPDF holds the samples
# buffer, the compression output, and the open document at the same time.
# Measured roughly 3x during PNG save.
PEAK_MULTIPLIER = 3.0

# Fraction of *currently available* RAM the pool may use.
MEMORY_FRACTION = 0.5

# Pages per worker before the whole pool is torn down and rebuilt. See the
# comment in render_set() — this replaces max_tasks_per_child, which deadlocks.
TASKS_PER_WORKER = 20


def _init(src: str) -> None:
    global _SRC
    _SRC = src


def pixmap_bytes(dpi: int, width_pt: float = 595.276, height_pt: float = 841.89) -> int:
    """Uncompressed RGB size of one rendered page. A4 at 600 DPI is ~104 MB."""
    scale = dpi / 72.0
    return int(width_pt * scale) * int(height_pt * scale) * 3


def available_memory() -> int | None:
    """Free RAM in bytes, or None if it can't be determined on this platform."""
    try:
        import psutil                                    # optional
        return psutil.virtual_memory().available
    except ImportError:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    try:
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        return None


def safe_workers(dpi: int, requested: int | None,
                 budget: int | None = None) -> tuple[int, str]:
    """
    Cap the pool against real available memory. Returns (workers, why).

    A fixed budget is the wrong model — 4 GB assumed against 104 MB pixmaps
    permits 38 workers and would never have prevented an out-of-memory
    failure at 8. What matters is how much RAM the machine actually has free
    right now, and that a worker's peak is roughly 3x the raw pixmap.
    """
    cpus = max(1, (os.cpu_count() or 2) - 1)
    want = requested or cpus
    per_worker = int(pixmap_bytes(dpi) * PEAK_MULTIPLIER)

    if budget is None:
        avail = available_memory()
        if avail is None:
            return want, "memory unknown on this platform; no cap applied"
        budget = int(avail * MEMORY_FRACTION)

    ceiling = max(1, budget // max(per_worker, 1))
    workers = max(1, min(want, ceiling))
    why = (f"{per_worker/1e6:.0f} MB peak per worker, "
           f"{budget/1e9:.1f} GB usable -> up to {ceiling}")
    return workers, why


def _render(job: tuple[int, int, str, int, str, int]) -> dict:
    """
    Render one page. Returns a plain dict, always. Never raises.

    Everything crossing the process boundary must be picklable. PyMuPDF
    exception objects are not, so the traceback is formatted to a string
    here, in the child, where the real error still exists.
    """
    pdf_page, orig_page, outdir, dpi, fmt, attempts = job
    dest = Path(outdir) / f"page_{orig_page:04d}.{fmt}"
    last = ""
    for attempt in range(1, attempts + 1):
        doc = pix = None
        try:
            doc = pymupdf.open(_SRC)
            pix = doc[pdf_page - 1].get_pixmap(dpi=dpi)
            if fmt == "jpg":
                pix.save(dest, jpg_quality=95)
            else:
                pix.save(dest)
            return {"ok": True, "page": orig_page, "path": str(dest),
                    "size": dest.stat().st_size, "attempts": attempt}
        except BaseException:
            # BaseException, not Exception: MemoryError is the expected
            # failure mode here and a killed allocation can surface in ways
            # a bare `except Exception` will miss.
            last = traceback.format_exc(limit=6)
        finally:
            # Free the ~104 MB pixmap before the next attempt or the next
            # task on this worker, rather than waiting for the collector.
            try:
                if pix is not None:
                    del pix
                if doc is not None:
                    doc.close()
            except BaseException:
                pass
    return {"ok": False, "page": orig_page, "path": str(dest),
            "size": 0, "attempts": attempts, "error": last}


def render_set(src: Path, jobs: list[tuple[int, int]], outdir: Path, dpi: int,
               fmt: str, workers: int, attempts: int = 2,
               resume: bool = False) -> tuple[int, list[dict]]:
    """jobs is (pdf_page, original_page). Returns (total_bytes, failures)."""
    outdir.mkdir(parents=True, exist_ok=True)

    if resume:
        before = len(jobs)
        jobs = [j for j in jobs if not (outdir / f"page_{j[1]:04d}.{fmt}").exists()]
        if before != len(jobs):
            print(f"  resume: skipping {before - len(jobs)} already rendered")

    payload = [(a, b, str(outdir), dpi, fmt, attempts) for a, b in jobs]
    if not payload:
        return 0, []

    total = done = 0
    failures: list[dict] = []

    # Workers are recycled in BATCHES, not by max_tasks_per_child.
    #
    # The intent is the same — no worker process accumulates fragmentation
    # across hundreds of 104 MB allocations — but max_tasks_per_child deadlocks
    # here. Combined with an `initializer` on Windows it hangs once the pool
    # retires its first generation of workers: runs stopped dead after exactly
    # workers x 20 pages (100, then 180, then 260 at different worker counts).
    # It was misdiagnosed as memory pressure twice, because a hang after N
    # pages looks exactly like one.
    #
    # A fresh pool per batch gives a stronger guarantee than
    # max_tasks_per_child did: every worker is torn down at the batch boundary
    # and all of its memory returns to the OS, deterministically, with no
    # dependence on the recycling machinery.
    batch = max(1, workers * TASKS_PER_WORKER)
    for start in range(0, len(payload), batch):
        chunk = payload[start:start + batch]
        with ProcessPoolExecutor(max_workers=workers, initializer=_init,
                                 initargs=(str(src),)) as ex:
            futures = [ex.submit(_render, p) for p in chunk]
            for fut in as_completed(futures):
                try:
                    res = fut.result()
                except BaseException:
                    # The pool itself died — a worker killed by the OOM killer
                    # never gets to return its dict.
                    res = {"ok": False, "page": -1, "size": 0,
                           "error": traceback.format_exc(limit=6)}
                if res["ok"]:
                    total += res["size"]
                    if res["attempts"] > 1:
                        print(f"  page {res['page']} succeeded on attempt "
                              f"{res['attempts']}", flush=True)
                else:
                    failures.append(res)
                    print(f"  FAILED page {res['page']}", flush=True)
                done += 1
                if done % 25 == 0 or done == len(payload):
                    print(f"  {done}/{len(payload)} rendered "
                          f"({total/1e6:.0f} MB, {len(failures)} failed)",
                          flush=True)
    return total, failures


def main() -> None:
    ap = argparse.ArgumentParser(description="Render split PDFs to high-DPI images.")
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--memory-budget-gb", type=float, default=None,
                    help="override the RAM budget; default is half of free memory")
    ap.add_argument("--no-worker-cap", action="store_true",
                    help="use --workers as given, ignoring the memory budget")
    ap.add_argument("--attempts", type=int, default=2,
                    help="tries per page before recording a failure")
    ap.add_argument("--resume", action="store_true",
                    help="skip pages whose image already exists")
    ap.add_argument("--pages", default=None,
                    help="comma-separated ORIGINAL page numbers; renders only these")
    ap.add_argument("--skip-ads", action="store_true")
    args = ap.parse_args()

    if args.no_worker_cap:
        workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
        why = "cap disabled"
    else:
        budget = (int(args.memory_budget_gb * 1024**3)
                  if args.memory_budget_gb else None)
        workers, why = safe_workers(args.dpi, args.workers, budget)
        if args.workers and workers < args.workers:
            print(f"note: capping {args.workers} workers to {workers} "
                  f"({why}). Override with --no-worker-cap.")

    content_pdf = args.outdir / "content.pdf"
    ads_pdf = args.outdir / "removed_ads.pdf"

    rows = list(csv.DictReader((args.outdir / "page_map.csv").open(encoding="utf-8")))
    jobs = [(int(r["content_pdf_page"]), int(r["original_page"])) for r in rows]
    wanted = {int(x) for x in args.pages.split(",")} if args.pages else None
    if wanted:
        jobs = [j for j in jobs if j[1] in wanted]

    all_failures: list[dict] = []

    print(f"rendering {len(jobs)} content pages at {args.dpi} DPI "
          f"({args.format}, {workers} workers; {why})")
    total, fails = render_set(content_pdf, jobs, args.outdir / "pages", args.dpi,
                              args.format, workers, args.attempts, args.resume)
    all_failures += fails
    print(f"pages/          {len(jobs) - len(fails)} images, {total/1e6:.0f} MB")

    if not args.skip_ads and ads_pdf.exists():
        plan = json.loads((args.outdir / "split_plan.json").read_text())
        ad_jobs = list(enumerate(plan["ad_pages"], start=1))
        if wanted:
            ad_jobs = [j for j in ad_jobs if j[1] in wanted]
        if ad_jobs:
            print(f"rendering {len(ad_jobs)} removed pages (verification set)")
            t2, f2 = render_set(ads_pdf, ad_jobs, args.outdir / "removed", args.dpi,
                                args.format, workers, args.attempts, args.resume)
            all_failures += f2
            print(f"removed/        {len(ad_jobs) - len(f2)} images, {t2/1e6:.0f} MB")

    if all_failures:
        report = args.outdir / "render_failures.json"
        report.write_text(json.dumps(all_failures, indent=2))
        print(f"\n{len(all_failures)} page(s) FAILED. Full tracebacks: {report}")
        print("first failure:\n")
        print(all_failures[0].get("error", "(no traceback captured)"))
        print("Re-run with --resume to retry only the missing pages, "
              "or lower --workers.")
        raise SystemExit(1)

    print("\nall pages rendered, no failures")


if __name__ == "__main__":
    main()
