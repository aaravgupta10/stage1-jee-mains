#!/usr/bin/env python3
"""Offline tests for jeelib. No PDF, no model, no cost.

Every case is a label this pipeline actually produced, right or wrong.

    python test_jeelib.py
"""
import sys

import jeelib as J

FAILS = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f"   {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def label(text, want, name):
    got = J.detect_paper(text)
    check(name, got == want, f"got {got!r}, want {want!r}")


print("\nPaper labels — the newline bug (A2)")
# \s matches a newline, so inside ^...$ the old patterns ran past the end of
# the line and captured body text. 2021 found 0 labels in 540 rows, 2023 0 in
# 1,080, 2025 322 in 675 and most of those were rubbish.
label("Set \n :\nSince the value of x is 5", "", "a label cannot span a newline")
label("Set \nB\n: For", "", "nor three lines")

print("\nPaper labels — prose that is not a label (2025)")
label("Set D to be the set of all\nvalues", "", "'Set D to' is body text, not a label")
label("Set A: For alpha the value is\n", "", "'Set A: For α' is body text")
label("Set B :\nFor alpha", "", "'Set B :' followed by prose is body text")
label("Shift 1 Question Paper with Solution\n", "",
      "a shift number with a title after it is a running header, not a label")

print("\nPaper labels — the real ones")
label("Set : A\nQ. 1 something", "Set : A", "a genuine set header on its own line")
label("Set A\nQ. 1", "Set A", "without punctuation")
label("2nd April - Shift 1 \nQuestion Paper with Solution\n JEE Main 2025 \n3",
      "2nd April - Shift 1", "the 2025 divider-page format")
label("8th April - Shift 2 \nQuestion Paper with Solution",
      "8th April - Shift 2", "the last shift in the 2025 file")
label("  Offline 16th April 2018\nQ. 1", "Offline 16th April 2018", "the 2018 format")
label("16th April 2018\nQ. 1", "16th April 2018", "bare date")
label("Shift - 2\nQ. 1", "Shift - 2", "bare shift")
label("Paper - 1\nQ. 1", "Paper - 1", "bare paper")
label("Morning Shift\nQ. 1", "Morning Shift", "named shift")

print("\nPaper labels — order")
# The date-shift form is checked first, so a page carrying both it and a
# stray "Set X" line is labelled with the real header.
label("3rd April - Shift 1 \nsomething\nSet A\n", "3rd April - Shift 1",
      "the divider header wins over a bare 'Set A' later on the page")

print("\nColour threshold (A1)")
p = J.Profile()
check("uncalibrated -> signal off", p.colour_threshold() is None)
p.content_colour_p95 = 0.0
check("a perfectly grey document still uses the 1% floor, not 10x0",
      p.colour_threshold() == 0.01)
p.content_colour_p95 = 0.0006      # the 2018 measurement
check("2018 baseline gives the 1% floor, inside the measured 0.27%-1.84% gap",
      p.colour_threshold() == 0.01)
p.content_colour_p95 = 0.00635     # the 2025 measurement
check("a more colourful document raises its own threshold",
      abs(p.colour_threshold() - 0.0635) < 1e-9, str(p.colour_threshold()))
check("colour alone cannot delete a page",
      p.colour_score < p.ad_score_delete,
      f"colour_score={p.colour_score} ad_score_delete={p.ad_score_delete}")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {', '.join(FAILS)}")
    sys.exit(1)
print("all jeelib tests passed")
