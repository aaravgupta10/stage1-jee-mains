#!/usr/bin/env python3
"""
Run the whole pipeline: calibrate -> classify -> split -> tag -> gate -> render.

    python run_all.py input.pdf -o out/ --dpi 600

Calibration runs first and writes profile.json. To reuse a profile you have
already reviewed (same publisher, new paper), pass --profile path/to/profile.json
and stage 0 is skipped.

Stages are separate scripts on purpose. Rendering is the expensive step
(~35 min single-threaded at 600 DPI) and you will re-run the cheap stages
while checking output. Use --skip-render while verifying.

--resume and --attempts are passed to BOTH stage 2 and stage 4, so re-running
the same command after a memory crash keeps whatever was already written and
retries only what is missing. --strategy goes to stage 2; --workers,
--no-worker-cap and --memory-budget-gb go to stage 4.
"""

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent

# A bare exit code is useless to whoever inherits this. 3221225477 means
# nothing until you know it is 0xC0000005, an access violation inside MuPDF,
# which in practice means the machine ran out of memory.
EXIT_CODES = {
    3221225477: ("0xC0000005 access violation (Windows) — a native crash "
                 "inside MuPDF, almost always memory pressure"),
    3221225725: "0xC00000FD stack overflow (Windows)",
    3221226505: "0xC0000409 stack buffer overrun (Windows)",
    -11: "SIGSEGV — a native crash inside MuPDF, almost always memory pressure",
    -9: "SIGKILL — killed by the OS, usually the out-of-memory killer",
    -6: "SIGABRT — abort inside a native library",
    137: "killed (128+9, SIGKILL) — usually the out-of-memory killer",
    139: "segfault (128+11, SIGSEGV) — usually memory pressure",
    1: "exited with an error (see the stage's own output above)",
}

# What to try, per stage, when that stage dies from memory pressure.
CRASH_ADVICE = {
    "02_split.py": ("  python 02_split.py INPUT.pdf -o OUTDIR --resume\n"
                    "  python 02_split.py INPUT.pdf -o OUTDIR "
                    "--strategy runs-nodeflate"),
    "04_render.py": ("  python 04_render.py -o OUTDIR --dpi 600 --resume\n"
                     "  python 04_render.py -o OUTDIR --dpi 600 --workers 2"),
}


def is_crash(code: int) -> bool:
    return code < 0 or code in (3221225477, 3221225725, 3221226505, 137, 139)


def run(script: str, *extra: str) -> None:
    print(f"\n=== {script} " + "=" * max(0, 58 - len(script)))
    t = time.time()
    r = subprocess.run([sys.executable, str(HERE / script), *extra])
    if r.returncode != 0:
        code = r.returncode
        meaning = EXIT_CODES.get(code, f"exited with code {code}")
        lines = [f"\n{script} FAILED — {meaning}"]
        if is_crash(code):
            lines.append(
                "\nThis was a CRASH, not an error. The process was killed "
                "outright, so no\nfailure report was written and nothing was "
                "logged by the stage itself.\nThe cause is almost certainly "
                "memory. Free some RAM, then:")
            lines.append(CRASH_ADVICE.get(script, "  re-run the stage on its own"))
        else:
            lines.append("\nThe stage exited normally with an error; its own "
                         "output above has the detail.")
        raise SystemExit("\n".join(lines))
    print(f"--- {script} finished in {time.time() - t:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the full pipeline.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("out"))
    ap.add_argument("--profile", type=Path, default=None,
                    help="reuse a reviewed profile instead of calibrating")
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--format", choices=["png", "jpg"], default="png")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--attempts", type=int, default=None,
                    help="tries per output (stage 2) and per page (stage 4)")
    ap.add_argument("--resume", action="store_true",
                    help="keep verified split halves and already-rendered pages")
    ap.add_argument("--strategy", choices=["select", "runs", "runs-nodeflate"],
                    default=None, help="force one stage-2 write strategy")
    ap.add_argument("--no-worker-cap", action="store_true",
                    help="use --workers as given, ignoring the memory budget")
    ap.add_argument("--memory-budget-gb", type=float, default=None,
                    help="override the render RAM budget (default: half of free memory)")
    ap.add_argument("--expect-per-section", type=int, default=None)
    ap.add_argument("--expect-sections", type=int, default=None)
    ap.add_argument("--no-strict", action="store_true")
    ap.add_argument("--skip-render", action="store_true")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    o = ["-o", str(args.outdir)]

    if args.profile:
        shutil.copy(args.profile, args.outdir / "profile.json")
        print(f"reusing profile {args.profile} (skipping calibration)")
    else:
        run("00_calibrate.py", str(args.pdf), *o)

    run("01_classify.py", str(args.pdf), *o)

    split = o[:]
    if args.attempts:
        split += ["--attempts", str(args.attempts)]
    if args.resume:
        split += ["--resume"]
    if args.strategy:
        split += ["--strategy", args.strategy]
    run("02_split.py", str(args.pdf), *split)

    tag = o[:]
    if args.expect_per_section:
        tag += ["--expect-per-section", str(args.expect_per_section)]
    if args.expect_sections:
        tag += ["--expect-sections", str(args.expect_sections)]
    if args.no_strict:
        tag += ["--no-strict"]
    run("03_tag_subjects.py", *tag)

    # The gate runs BEFORE the render, not after. Rendering is the expensive
    # stage — roughly 35 minutes at 600 DPI — and there is no point spending it
    # on an index with duplicate ids in it, because stage 2 will then overwrite
    # one question with another and neither the render nor the reconciliation
    # will say a word. The 2025 paper lost 171 questions exactly that way.
    gate = o[:]
    if args.no_strict:
        gate += ["--no-strict"]
    run("05_gate_report.py", *gate)

    if args.skip_render:
        print("\nskipping render (--skip-render)")
    else:
        rend = o + ["--dpi", str(args.dpi), "--format", args.format]
        if args.workers:
            rend += ["--workers", str(args.workers)]
        if args.attempts:
            rend += ["--attempts", str(args.attempts)]
        if args.resume:
            rend += ["--resume"]
        if args.no_worker_cap:
            rend += ["--no-worker-cap"]
        if args.memory_budget_gb:
            rend += ["--memory-budget-gb", str(args.memory_budget_gb)]
        run("04_render.py", *rend)

    print(f"\ndone -> {args.outdir.resolve()}")


if __name__ == "__main__":
    main()
