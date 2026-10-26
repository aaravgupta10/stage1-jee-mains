#!/usr/bin/env python3
"""
Stage 2 — split the source into content.pdf and removed_ads.pdf.

Both halves are written. Nothing is destroyed, so any misclassification is
recoverable and auditable. page_map.csv is the thread back to the source:
every page of content.pdf records which original page it came from, and every
later stage carries the ORIGINAL page number in its filenames.

CRASH RESILIENCE
----------------
MuPDF can segfault under memory pressure (Windows 0xC0000005 access
violation, POSIX SIGSEGV). A segfault cannot be caught in-process: there is
no exception to trap, the interpreter is simply gone. So the actual write
runs in a CHILD process that the parent can outlive, watch, and retry. This
is the same failure mode stage 4 hit, but it needs a different remedy —
stage 4 could catch its exceptions, stage 2 cannot.

Retrying the identical allocation that just died is not much of a strategy,
so attempts ESCALATE to progressively lower-memory approaches:

  1. select      — prune the page tree, save whole. Fastest (~1 s), highest
                   peak memory.
  2. runs        — copy contiguous page runs into a fresh document. ~70
                   allocations instead of one large one. Slower, much
                   flatter memory profile.
  3. runs-nodeflate — same, with compression off. Deflate buffers are a
                   large part of the peak. Biggest file, lowest memory.

Every attempt is VERIFIED by reopening the output and checking its page
count, because a process killed mid-write leaves a plausible-looking
truncated PDF that would otherwise flow silently into stage 3.

    python 02_split.py input.pdf -o out/
    python 02_split.py input.pdf -o out/ --resume      # skip verified outputs
    python 02_split.py input.pdf -o out/ --strategy runs --attempts 1
"""

import argparse
import csv
import json
import multiprocessing as mp
import os
import sys
import traceback
from pathlib import Path

import pymupdf

STRATEGIES = ["select", "runs", "runs-nodeflate"]


# --------------------------------------------------------------------------
# Diagnosis of child exit codes
# --------------------------------------------------------------------------

CRASH_CODES = {
    3221225477: "0xC0000005 access violation (Windows) — MuPDF crashed, "
                "almost always memory pressure",
    3221225725: "0xC00000FD stack overflow (Windows)",
    3221226505: "0xC0000409 stack buffer overrun (Windows)",
    -11: "SIGSEGV — MuPDF crashed, almost always memory pressure",
    -9: "SIGKILL — killed by the OS, usually the out-of-memory killer",
    -6: "SIGABRT — abort inside a native library",
    137: "killed (128+9, SIGKILL) — usually the out-of-memory killer",
    139: "segfault (128+11, SIGSEGV) — usually memory pressure",
}


def is_crash(code: int) -> bool:
    """
    Did the child die, as opposed to exiting with an error?

    The distinction drives the advice given at the end. A crash means memory
    and retrying will probably help. A clean non-zero exit means a Python
    exception — a bad file or a bad plan — and retrying will not.
    """
    return code in CRASH_CODES or code < 0


def describe_exit(code: int) -> str:
    """Turn a raw exit code into something a person can act on."""
    if code in CRASH_CODES:
        return CRASH_CODES[code]
    if code < 0:
        return f"killed by signal {-code}"
    return f"exited with code {code} (an exception, not a crash)"


def free_memory_gb() -> float | None:
    try:
        import psutil
        return psutil.virtual_memory().available / 1024**3
    except ImportError:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024 / 1024**3
    except OSError:
        pass
    try:
        return (os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")) / 1024**3
    except (ValueError, OSError, AttributeError):
        return None


# --------------------------------------------------------------------------
# The writers — these run inside the child process
# --------------------------------------------------------------------------


def _contiguous_runs(pages: list[int]) -> list[tuple[int, int]]:
    """[1,2,3,7,8] -> [(1,3),(7,8)]. Fewer, larger copies than page-by-page."""
    runs: list[tuple[int, int]] = []
    for p in pages:
        if runs and p == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], p)
        else:
            runs.append((p, p))
    return runs


def _write(src: str, pages: list[int], dest: str, strategy: str,
           compress: bool) -> None:
    """Write the subset. Runs in the child process. May die outright."""
    if strategy == "select":
        doc = pymupdf.open(src)
        doc.select([p - 1 for p in pages])
        if compress:
            doc.save(dest, deflate=True, garbage=4, clean=True)
        else:
            doc.save(dest, deflate=True)
        doc.close()
        return

    # "runs" strategies: build a fresh document from contiguous ranges.
    # Peak memory is one run at a time rather than the whole document.
    deflate = strategy != "runs-nodeflate"
    src_doc = pymupdf.open(src)
    out = pymupdf.open()
    for a, b in _contiguous_runs(pages):
        out.insert_pdf(src_doc, from_page=a - 1, to_page=b - 1)
    out.save(dest, deflate=deflate)
    out.close()
    src_doc.close()


def _child(src: str, pages: list[int], dest: str, strategy: str,
           compress: bool, err_q) -> None:
    try:
        _write(src, pages, dest, strategy, compress)
    except BaseException:
        try:
            err_q.put(traceback.format_exc(limit=6))
        except BaseException:
            pass
        sys.exit(1)


# --------------------------------------------------------------------------
# Parent-side orchestration
# --------------------------------------------------------------------------


def verify(dest: Path, expected_pages: int) -> tuple[bool, str]:
    """
    Reopen the output and check it. A process killed mid-write leaves a
    truncated file that still looks like a PDF; without this it would flow
    silently into stage 3.
    """
    if not dest.exists():
        return False, "output file not created"
    if dest.stat().st_size == 0:
        return False, "output file is empty"
    try:
        doc = pymupdf.open(dest)
        n = doc.page_count
        doc.close()
    except BaseException as exc:
        return False, f"output will not open: {type(exc).__name__}: {exc}"
    if n != expected_pages:
        return False, f"output has {n} pages, expected {expected_pages}"
    return True, f"{n} pages verified"


def write_subset(src: Path, pages: list[int], dest: Path, label: str,
                 attempts: int = 3, compress: bool = False,
                 strategy: str | None = None,
                 resume: bool = False) -> dict:
    """
    Write pages to dest, surviving child crashes. Returns a result dict.

    The write happens in a child process so that a MuPDF segfault kills the
    child, not this script. Attempts escalate to lower-memory strategies
    rather than repeating the allocation that just died.
    """
    if resume:
        ok, why = verify(dest, len(pages))
        if ok:
            print(f"  {label}: resume — existing output {why}, skipping")
            return {"ok": True, "resumed": True, "pages": len(pages),
                    "strategy": "resume"}
        if dest.exists():
            print(f"  {label}: existing output rejected ({why}), rewriting")

    plan = [strategy] * attempts if strategy else STRATEGIES[:attempts]
    if len(plan) < attempts:
        plan += [plan[-1]] * (attempts - len(plan))

    history: list[dict] = []
    ctx = mp.get_context("spawn")          # required on Windows, safe elsewhere

    for i, strat in enumerate(plan, start=1):
        free = free_memory_gb()
        free_txt = f", {free:.2f} GB free" if free is not None else ""
        print(f"  {label}: attempt {i}/{attempts} using '{strat}'{free_txt}",
              flush=True)
        if free is not None and free < 1.0:
            print(f"  {label}: WARNING only {free:.2f} GB free — MuPDF may crash",
                  flush=True)

        err_q = ctx.Queue()
        proc = ctx.Process(target=_child,
                           args=(str(src), pages, str(dest), strat, compress, err_q))
        proc.start()
        proc.join()
        code = proc.exitcode

        child_err = ""
        try:
            if not err_q.empty():
                child_err = err_q.get_nowait()
        except BaseException:
            pass

        if code == 0:
            ok, why = verify(dest, len(pages))
            if ok:
                print(f"  {label}: {why} (strategy '{strat}')")
                return {"ok": True, "resumed": False, "pages": len(pages),
                        "strategy": strat, "attempt": i, "history": history}
            reason = f"child exited cleanly but output invalid: {why}"
        else:
            reason = describe_exit(code)
            if child_err:
                reason += f"\n{child_err}"

        headline = reason.splitlines()[0]
        if child_err.strip():
            headline += f" | {child_err.strip().splitlines()[-1]}"
        print(f"  {label}: attempt {i} FAILED — {headline}", flush=True)
        detail = child_err.strip().splitlines()[-1] if child_err.strip() else ""
        history.append({"attempt": i, "strategy": strat, "exit_code": code,
                        "crashed": is_crash(code), "reason": reason,
                        "exception": detail})
        # A partial file would be mistaken for a real output by --resume.
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass

    return {"ok": False, "pages": len(pages), "history": history}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Split content pages from advertisement pages.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--compress", action="store_true",
                    help="reclaim orphaned image data (~90s, saves ~2 MB)")
    ap.add_argument("--attempts", type=int, default=3,
                    help="tries per output, escalating to lower-memory strategies")
    ap.add_argument("--strategy", choices=STRATEGIES, default=None,
                    help="force one strategy instead of escalating")
    ap.add_argument("--resume", action="store_true",
                    help="keep existing outputs that pass verification")
    args = ap.parse_args()

    plan = json.loads((args.outdir / "split_plan.json").read_text())
    keep, ads = plan["keep_pages"], plan["ad_pages"]

    # Refuse to proceed unless every source page is accounted for exactly once.
    # A silent off-by-one here would lose answers with no trace.
    if sorted(keep + ads) != list(range(1, plan["total_pages"] + 1)):
        raise SystemExit("split plan does not account for every page — refusing to split")

    free = free_memory_gb()
    if free is not None:
        print(f"free memory: {free:.2f} GB")
        if free < 1.0:
            print("WARNING: under 1 GB free. MuPDF segfaults under memory "
                  "pressure; the low-memory strategies should still get "
                  "through, but close something if you can.")

    results = {}
    results["content.pdf"] = write_subset(
        args.pdf, keep, args.outdir / "content.pdf", "content.pdf",
        args.attempts, args.compress, args.strategy, args.resume)

    if ads:
        results["removed_ads.pdf"] = write_subset(
            args.pdf, ads, args.outdir / "removed_ads.pdf", "removed_ads.pdf",
            args.attempts, args.compress, args.strategy, args.resume)

    failed = {k: v for k, v in results.items() if not v["ok"]}
    if failed:
        report = args.outdir / "split_failures.json"
        report.write_text(json.dumps(failed, indent=2))
        print(f"\nSPLIT FAILED for: {', '.join(failed)}")
        print(f"Full attempt history: {report}\n")
        any_crash = False
        for name, res in failed.items():
            for h in res["history"]:
                any_crash = any_crash or h.get("crashed", False)
                line = f"  {name} attempt {h['attempt']} ({h['strategy']}): " \
                       f"{h['reason'].splitlines()[0]}"
                if h.get("exception"):
                    line += f"\n      -> {h['exception']}"
                print(line)

        # The advice has to match the actual failure. Telling someone to free
        # RAM when their PDF is corrupt sends them down the wrong path, which
        # is the exact problem this whole stage was rewritten to avoid.
        if any_crash:
            print("\nAt least one attempt CRASHED rather than raising. That is a "
                  "memory problem, not a data problem.\n"
                  "  - free some RAM and re-run with --resume\n"
                  "  - or force the lowest-memory path:\n"
                  "      python 02_split.py input.pdf -o out/ --strategy runs-nodeflate")
        else:
            print("\nEvery attempt raised a normal exception — no crash. This is a "
                  "problem with the FILE or the PLAN, not with memory; retrying "
                  "or freeing RAM will not help.\n"
                  "  - check the source PDF opens: python -c "
                  "\"import pymupdf;print(pymupdf.open('input.pdf').page_count)\"\n"
                  "  - check split_plan.json page numbers match that count\n"
                  f"  - full tracebacks are in {report}")
        raise SystemExit(1)

    # page_map is only written once the PDFs are known good, so a failed run
    # never leaves a mapping that points at a file that isn't there.
    with (args.outdir / "page_map.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["content_pdf_page", "original_page"])
        for i, p in enumerate(keep, start=1):
            w.writerow([i, p])

    size = (args.outdir / "content.pdf").stat().st_size / 1e6
    print(f"\ncontent.pdf      {len(keep):>4} pages  ({size:.1f} MB)")
    if ads:
        asz = (args.outdir / "removed_ads.pdf").stat().st_size / 1e6
        print(f"removed_ads.pdf  {len(ads):>4} pages  ({asz:.1f} MB)")
    print(f"page_map.csv     {len(keep)} rows")


if __name__ == "__main__":
    main()
