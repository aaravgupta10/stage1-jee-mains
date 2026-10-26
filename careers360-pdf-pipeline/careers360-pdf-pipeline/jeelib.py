"""
jeelib.py (v2) — feature extraction, calibration and classification.

v2 removes the assumptions that break across papers from the same publisher:

  * Question count per section is no longer assumed (was hardcoded 30).
    Sections are found from numbering resets and their sizes are reported,
    not asserted.
  * Shift/paper count is no longer assumed. Headers are matched against a
    family of patterns and fall back to ordinal naming.
  * Advertisement detection no longer leans on a keyword list, which rots
    every time the publisher changes campaign. The primary signal is now
    page repetition: ads are near-duplicate pages repeated through the
    document, and a real question never is. Measured on the 2018 JEE file:
    4 clusters capture 68 of 69 ads with zero false positives.
  * Thresholds are calibrated from each document's own distributions
    instead of being constants measured on one file.

v3 adds one more advertisement signal: colour.

  Measured on 14 advertisement pages and 14 content pages from the 2018
  document, at 50 DPI, counting pixels where max(R,G,B) - min(R,G,B) > 40:

      advertisement pages   median 29.63%   range 1.84% - 42.52%
      content pages         median  0.06%   range 0.00% -  0.27%

  Complete separation, with the least colourful advertisement about seven
  times more coloured than the most colourful content page. The baseline is
  calibrated per document, not hardcoded, and colour only ADDS to a score
  that already needs corroboration. A content page carrying a large coloured
  figure must not be deletable on colour alone.
"""

from __future__ import annotations

import collections
import hashlib
import json
import re
import statistics
from dataclasses import dataclass, asdict, field

import pymupdf

try:                                     # only used to speed up colour_frac()
    import numpy as np
except ImportError:                      # pragma: no cover
    np = None

# --------------------------------------------------------------------------
# Candidate banks — the tool picks from these, it does not assume one
# --------------------------------------------------------------------------

# Question-numbering patterns. The correct one produces monotonic +1 chains;
# wrong ones produce noise. That makes the choice self-validating.
QUESTION_PATTERNS: dict[str, str] = {
    "Q. N":       r"Q\.\s*(\d{1,4})",
    "Q N":        r"\bQ\s+(\d{1,4})\b",
    "QN)":        r"\bQ(\d{1,4})\s*[\).]",
    "Question N": r"Question\s*[-:.]?\s*(\d{1,4})",
    "Ques. N":    r"Ques\.?\s*(\d{1,4})",
    "N. inline":  r"(?<![\d.])(\d{1,4})\.\s+[A-Z]",
    "N.":         r"(?m)^\s*(\d{1,4})\s*\.",
    "N)":         r"(?m)^\s*(\d{1,4})\s*\)",
    "(N)":        r"(?m)^\s*\((\d{1,4})\)",
    "Sol. N":     r"Sol\.?\s*(\d{1,4})",
}

# Secondary content labels. Any of these on a page grants immunity from
# deletion, which is what protects diagram-only and image-only questions.
MARKER_PATTERNS: dict[str, str] = {
    "option_numbered": r"Option\s*[1-9]\s*:",
    "option_lettered": r"(?m)^\s*\(?[A-D]\)?[.)]\s+\S",
    "correct_answer":  r"Correct\s+Answer\s*:",
    "answer":          r"\bAns(?:wer)?\s*[:.]",
    "solution":        r"\bSolution\s*:",
    "sol":             r"\bSol\.\s",
    "explanation":     r"\bExplanation\s*:",
}

# Paper / shift headers. Broadened well past "Offline 16th April" because
# shift naming varies between papers.
#
# A2: these use [ \t] throughout, never \s.
#
# \s matches a newline. Inside ^...$ with re.MULTILINE that is not harmless:
# the anchors mark line boundaries, but \s* happily consumes the boundary and
# carries the match onto the following line, so the pattern reads body text as
# a label. On the literal string "Set \n :\nSince the value of x", the \s form
# captures the whole fragment; [ \t] captures nothing, while still matching a
# genuine "Set : A" written with spaces or tabs.
#
# Measured: 2021 found 0 labels in 540 rows, 2023 0 in 1,080, and 2025 found
# 322 in 675 — of which many were rubbish like "Set A: For alpha". The label
# fed q_id at the time, which is how 171 questions ended up sharing an id.
# A pattern must capture the label and NOTHING ELSE. Where a line can carry
# prose after the label, the capture stops at the label and the pattern does
# not anchor to the end of the line; where the whole line IS the label, it
# anchors. The old `[^\n]{0,N}$` tails are what produced "Set D to" and
# "Set A: For α" as paper labels on the 2025 paper — both are body text.
PAPER_PATTERNS: list[str] = [
    # "2nd April - Shift 1", the form this publisher uses on divider pages.
    # It appears on the 9 divider pages of the 2025 file and nowhere else, and
    # 03_tag_subjects carries the last seen label forward, so 9 matches label
    # all 675 questions. The line continues "Question Paper with Solution", so
    # the capture stops after the shift number and there is no $ anchor.
    r"(?m)^[ \t]*(\d{1,2}(?:st|nd|rd|th)[ \t]+[A-Z][a-z]+[ \t]*[-–—][ \t]*"
    r"Shift[ \t]*[0-9IVX]+)",
    r"(?m)^[ \t]*((?:Off|On)line[^\n]{0,60})$",
    r"(?m)^[ \t]*(\d{1,2}(?:st|nd|rd|th)?[ \t]+[A-Z][a-z]+[ \t]+\d{4})[ \t]*$",
    r"(?m)^[ \t]*((?:Morning|Evening|Afternoon|First|Second)[ \t]+Shift"
    r"(?:[ \t]*[0-9IVX]+)?)[ \t]*$",
    r"(?m)^[ \t]*(Shift[ \t]*[-:]?[ \t]*[0-9IVX]+)[ \t]*$",
    r"(?m)^[ \t]*(Paper[ \t]*[-:]?[ \t]*[0-9IVX]+)[ \t]*$",
    r"(?m)^[ \t]*(Set[ \t]*[-:]?[ \t]*[A-Z0-9]{1,3})[ \t]*$",
]

# Subject vocabulary for this publisher's exam families (JEE Main paper 1
# and 2, NEET, BITSAT). Unrecognised section names still get a positional
# label, so an unlisted subject degrades to "section_4" rather than failing.
SUBJECT_CANON: dict[str, str] = {
    "physics": "Physics",
    "chemistry": "Chemistry",
    "mathematics": "Mathematics",
    "maths": "Mathematics",
    "math": "Mathematics",
    "biology": "Biology",
    "botany": "Botany",
    "zoology": "Zoology",
    "aptitude": "Aptitude",
    "general aptitude": "Aptitude",
    "drawing": "Drawing",
    "planning": "Planning",
    "reasoning": "Reasoning",
    "logical reasoning": "Logical Reasoning",
    "english": "English",
    "english proficiency": "English",
}
SUBJECT_HEADER = re.compile(
    r"(?m)^\s*(" + "|".join(sorted(map(re.escape, SUBJECT_CANON), key=len, reverse=True))
    + r")\s*$",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# Profile — everything the classifier needs, derived per document
# --------------------------------------------------------------------------


@dataclass
class Profile:
    question_pattern_name: str = "Q. N"
    question_regex: str = QUESTION_PATTERNS["Q. N"]
    marker_regexes: list[str] = field(default_factory=list)
    numbering_regime: str = "unknown"      # resets_per_section | continuous
    watermark_frac: list[float] | None = None
    draw_ops_high: int = 50
    ad_cluster_pages: list[int] = field(default_factory=list)
    ad_score_delete: int = 3
    # --- colour signal (v3) ------------------------------------------------
    # content_colour_p95 is the 95th percentile coloured-pixel fraction over
    # this document's own content pages. None means calibration could not
    # measure it and the signal is off. A page scores only above
    # max(p95 * colour_multiple, colour_floor): the floor exists because p95
    # is legitimately 0.0000 on a clean document, and ten times zero is zero,
    # which would flag every page carrying a single coloured pixel. 1% sits in
    # the middle of the measured gap between 0.27% and 1.84%.
    content_colour_p95: float | None = None
    colour_multiple: float = 10.0
    colour_floor: float = 0.01
    colour_score: int = 2
    colour_dpi: int = 50
    colour_sat: int = 40
    min_cluster: int = 3
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def colour_threshold(self) -> float | None:
        """Coloured-pixel fraction above which a page scores. None = signal off."""
        if self.content_colour_p95 is None:
            return None
        return max(self.content_colour_p95 * self.colour_multiple, self.colour_floor)

    def content_marker(self) -> re.Pattern:
        # Inline (?m) flags are illegal anywhere but the start of a pattern,
        # so strip them and set MULTILINE on the combined expression instead.
        parts = [self.question_regex] + self.marker_regexes
        cleaned = [p.replace("(?m)", "") for p in parts]
        return re.compile("|".join(f"(?:{p})" for p in cleaned),
                          re.IGNORECASE | re.MULTILINE)

    def question_num(self) -> re.Pattern:
        return re.compile(self.question_regex)

    def save(self, path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)

    @classmethod
    def load(cls, path) -> "Profile":
        with open(path, encoding="utf-8") as fh:
            return cls(**json.load(fh))


# --------------------------------------------------------------------------
# Structural fingerprint (for repetition clustering)
# --------------------------------------------------------------------------


def fingerprint(page: pymupdf.Page) -> str:
    """
    A layout hash that is stable across identical template pages but differs
    between distinct questions. Uses image count, coarsely rounded image
    positions, and a bucketed vector-op count. Deliberately ignores text, so
    it works on untexted pages and on any language.
    """
    boxes = sorted(
        tuple(round(c / 10) for c in info["bbox"]) for info in page.get_image_info()
    )
    payload = json.dumps([len(boxes), boxes, len(page.get_drawings()) // 10])
    return hashlib.md5(payload.encode()).hexdigest()[:10]


# --------------------------------------------------------------------------
# Per-page features
# --------------------------------------------------------------------------

AD_KEYWORD = re.compile(
    r"(careers360|knockout|rank\s*booster|b\.?tech\s+companion|counselling"
    r"|college\s+predictor|bit\.ly|\.com|enroll|subscribe|crash\s+course"
    r"|scholarship|webinar|almanac|download\s+(?:the\s+)?app)",
    re.IGNORECASE,
)


@dataclass
class PageFeatures:
    page: int
    chars: int = 0
    markers: int = 0
    ad_keywords: int = 0
    links: int = 0
    draw_ops: int = 0
    n_images: int = 0
    img_frac_total: float = 0.0
    img_frac_largest: float = 0.0
    colour_frac: float = 0.0
    has_watermark: bool = False
    fp: str = ""
    fp_cluster_size: int = 1
    in_ad_cluster: bool = False
    outside_question_range: bool = False
    question_nums: list[int] = field(default_factory=list)
    subjects: list[str] = field(default_factory=list)
    paper: str = ""
    head: str = ""
    verdict: str = ""
    ad_score: int = 0
    reason: str = ""

    def as_row(self) -> dict:
        d = asdict(self)
        d["question_nums"] = " ".join(map(str, self.question_nums))
        d["subjects"] = " ".join(self.subjects)
        return d


def colour_frac(page: pymupdf.Page, dpi: int = 50, sat: int = 40) -> float:
    """Fraction of pixels that carry real colour, not just grey.

    Rendered at 50 DPI, which is enough: this measures how much of the page is
    coloured, not what the colour is, and a low DPI keeps a 600-page document
    to a few seconds. A pixel counts as coloured when max(R,G,B) - min(R,G,B)
    exceeds `sat`, so black text, grey rules and the grey watermark all score
    zero however dark they are, while a brand-coloured banner scores high.

    Returns 0.0 for a page that cannot be rendered rather than raising: this
    is one signal among several and an unrenderable page is a problem for the
    renderer to report, not a reason to abandon classification.
    """
    try:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    except Exception:
        return 0.0
    buf = pix.samples
    n = pix.width * pix.height
    if n <= 0:
        return 0.0
    if np is not None:
        a = np.frombuffer(buf, dtype=np.uint8).reshape(-1, 3).astype(np.int16)
        return float(((a.max(axis=1) - a.min(axis=1)) > sat).mean())
    # Fallback for an install without numpy. Correct, but roughly a hundred
    # times slower, so it is worth having numpy for a 600-page document.
    coloured = 0
    for i in range(0, len(buf), 3):
        r, g, b = buf[i], buf[i + 1], buf[i + 2]
        if max(r, g, b) - min(r, g, b) > sat:
            coloured += 1
    return coloured / n


def _img_fracs(page: pymupdf.Page) -> list[float]:
    area = abs(page.rect.get_area()) or 1.0
    out = []
    for info in page.get_image_info():
        clipped = pymupdf.Rect(info["bbox"]) & page.rect
        if not clipped.is_empty:
            out.append(abs(clipped.get_area()) / area)
    return out


def extract_features(page: pymupdf.Page, prof: Profile) -> PageFeatures:
    text = page.get_text()
    fracs = _img_fracs(page)
    f = PageFeatures(page=page.number + 1)
    f.chars = len(text.strip())
    f.markers = len(prof.content_marker().findall(text))
    f.ad_keywords = len(AD_KEYWORD.findall(text))
    f.links = len(page.get_links())
    f.draw_ops = len(page.get_drawings())
    f.n_images = len(fracs)
    f.img_frac_total = round(sum(fracs), 4)
    f.img_frac_largest = round(max(fracs, default=0.0), 4)
    f.colour_frac = round(colour_frac(page, prof.colour_dpi, prof.colour_sat), 4)
    if prof.watermark_frac:
        lo, hi = prof.watermark_frac
        f.has_watermark = any(lo <= x <= hi for x in fracs)
    f.fp = fingerprint(page)
    f.question_nums = [int(m.group(1)) for m in prof.question_num().finditer(text)]
    f.subjects = [SUBJECT_CANON[m.group(1).lower()] for m in SUBJECT_HEADER.finditer(text)]
    f.paper = detect_paper(text)
    f.head = " | ".join(text.strip().splitlines()[:3])[:90]
    return f


def detect_paper(text: str) -> str:
    for pat in PAPER_PATTERNS:
        m = re.search(pat, text)
        if m:
            cand = " ".join(m.group(1).split())
            if cand.lower() not in SUBJECT_CANON:
                return cand
    return ""


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------


def _score_pattern(regex: str, texts: list[str]) -> dict:
    """Score a candidate numbering pattern by sequence coherence."""
    seq: list[int] = []
    for t in texts:
        seq += [int(m.group(1)) for m in re.finditer(regex, t)]
    ded: list[int] = []
    for n in seq:
        if not ded or ded[-1] != n:
            ded.append(n)
    if len(ded) < 5:
        return {"hits": len(ded), "longest": 0, "coherence": 0.0, "score": 0.0, "runs": 0}
    runs, cur = [], 1
    for a, b in zip(ded, ded[1:]):
        if b == a + 1:
            cur += 1
        else:
            runs.append(cur)
            cur = 1
    runs.append(cur)
    longest = max(runs)
    coherence = sum(r for r in runs if r >= 5) / len(ded)
    return {"hits": len(ded), "longest": longest, "coherence": round(coherence, 4),
            "score": round(coherence * longest, 2), "runs": len(runs)}


def calibrate(doc: pymupdf.Document, min_cluster: int = 3) -> Profile:
    """Derive a document-specific profile. No constants from other files."""
    texts = [p.get_text() for p in doc]
    prof = Profile(min_cluster=min_cluster)
    warn = prof.warnings

    # --- 1. question numbering pattern -----------------------------------
    scored = {name: _score_pattern(rx, texts) for name, rx in QUESTION_PATTERNS.items()}
    best = max(scored, key=lambda k: scored[k]["score"])
    runner = sorted(scored, key=lambda k: -scored[k]["score"])[1]
    prof.question_pattern_name = best
    prof.question_regex = QUESTION_PATTERNS[best]
    prof.stats["question_pattern_scores"] = scored
    if scored[best]["score"] <= 0:
        warn.append("no question-numbering pattern matched; add one to QUESTION_PATTERNS")
    elif scored[best]["score"] < 2 * max(scored[runner]["score"], 0.01):
        warn.append(f"pattern choice is not decisive: {best}={scored[best]['score']} "
                    f"vs {runner}={scored[runner]['score']} — verify manually")
    if scored[best]["coherence"] < 0.9:
        warn.append(f"numbering coherence only {scored[best]['coherence']:.0%}; "
                    "questions may be missed or double-counted")

    # --- 2. secondary content markers present in this document -----------
    for name, rx in MARKER_PATTERNS.items():
        hits = sum(len(re.findall(rx, t, re.IGNORECASE)) for t in texts)
        if hits >= max(10, doc.page_count // 20):
            prof.marker_regexes.append(rx)
            prof.stats.setdefault("marker_hits", {})[name] = hits
    if not prof.marker_regexes:
        warn.append("no secondary content markers found; immunity rests on the "
                    "question pattern alone")

    # --- 3. which pages are definitely content (used to calibrate on) ----
    qre = prof.question_num()
    content_idx = [i for i, t in enumerate(texts) if qre.search(t)]
    if len(content_idx) < 10:
        warn.append("too few question-bearing pages to calibrate thresholds; "
                    "falling back to defaults")
        return prof

    # --- 4. watermark coverage band --------------------------------------
    # The template watermark is the image size that recurs on nearly every
    # content page. Find it empirically rather than assuming 0.55-0.85.
    buckets: collections.Counter = collections.Counter()
    for i in content_idx:
        for fr in set(round(x, 2) for x in _img_fracs(doc[i])):
            buckets[fr] += 1
    if buckets:
        mode_frac, n = buckets.most_common(1)[0]
        share = n / len(content_idx)
        prof.stats["watermark"] = {"frac": mode_frac, "present_on": round(share, 3)}
        if share >= 0.7:
            prof.watermark_frac = [round(mode_frac - 0.08, 3), round(mode_frac + 0.08, 3)]
        else:
            warn.append(f"no consistent template image (best covers only "
                        f"{share:.0%} of content pages); watermark signal disabled")

    # --- 5. vector-op threshold ------------------------------------------
    ops = sorted(len(doc[i].get_drawings()) for i in content_idx)
    p99 = ops[min(len(ops) - 1, int(0.99 * len(ops)))]
    prof.draw_ops_high = max(20, int(p99 * 1.2) + 5)
    prof.stats["draw_ops"] = {"content_median": statistics.median(ops),
                              "content_p99": p99, "threshold": prof.draw_ops_high}

    # --- 5b. colour baseline of THIS document's content pages -------------
    # Sampled rather than exhaustive: 40 pages pin a p95 well enough and keep
    # calibration to a couple of seconds on a 600-page file. The pages are
    # taken evenly across the document so a colour change partway through --
    # a different shift printed on different stock -- is inside the sample.
    step = max(1, len(content_idx) // 40)
    sample_idx = content_idx[::step][:40]
    cols = sorted(colour_frac(doc[i], prof.colour_dpi, prof.colour_sat)
                  for i in sample_idx)
    if cols:
        prof.content_colour_p95 = round(cols[min(len(cols) - 1,
                                                 int(0.95 * len(cols)))], 5)
        prof.stats["colour"] = {
            "sampled_content_pages": len(cols),
            "content_median": round(statistics.median(cols), 5),
            "content_p95": prof.content_colour_p95,
            "content_max": round(cols[-1], 5),
            "threshold": round(prof.colour_threshold(), 5),
            "score_added": prof.colour_score,
        }
        if prof.content_colour_p95 > prof.colour_floor:
            warn.append(f"content pages are unusually colourful "
                        f"(p95 {prof.content_colour_p95:.2%}); the colour signal "
                        f"will only fire above "
                        f"{prof.colour_threshold():.2%} and may never fire")

    # --- 6. repeated ad templates ----------------------------------------
    groups: dict[str, list[int]] = collections.defaultdict(list)
    for i, page in enumerate(doc):
        groups[fingerprint(page)].append(i + 1)
    content_pages = {i + 1 for i in content_idx}
    clusters = {}
    for h, pages in groups.items():
        if len(pages) >= min_cluster and not (set(pages) & content_pages):
            clusters[h] = pages
            prof.ad_cluster_pages.extend(pages)
    prof.ad_cluster_pages.sort()
    prof.stats["ad_clusters"] = {h: len(p) for h, p in clusters.items()}
    if not clusters:
        warn.append("no repeated page templates found; ad detection will rely "
                    "on hyperlinks and keywords only")

    # --- 7. numbering regime ---------------------------------------------
    nums: list[int] = []
    for t in texts:
        nums += [int(m.group(1)) for m in qre.finditer(t)]
    ded = [n for k, n in enumerate(nums) if k == 0 or nums[k - 1] != n]
    resets = sum(1 for a, b in zip(ded, ded[1:]) if b <= a)
    prof.numbering_regime = "resets_per_section" if resets else "continuous"
    prof.stats["numbering"] = {"total_markers": len(ded), "resets": resets}
    return prof


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def classify_pages(feats: list[PageFeatures], prof: Profile) -> list[PageFeatures]:
    """
    Rule 1 (immunity):    any content marker -> CONTENT, unconditionally.
    Rule 2 (scoring):     markerless pages accumulate evidence; deletion
                          needs >= ad_score_delete so no single signal can
                          drop a page alone.
    Rule 3 (continuation): markerless, no evidence -> spillover solution,
                          inherits the previous page's verdict.
    Rule 4 (fail safe):   ambiguous -> REVIEW, and REVIEW is KEPT.
    """
    cluster_sizes = collections.Counter(f.fp for f in feats)
    ad_cluster = set(prof.ad_cluster_pages)

    marker_pages = [f.page for f in feats if f.markers > 0]
    first_q = min(marker_pages, default=1)
    last_q = max(marker_pages, default=len(feats))

    for f in feats:
        f.fp_cluster_size = cluster_sizes[f.fp]
        f.in_ad_cluster = f.page in ad_cluster
        f.outside_question_range = f.page < first_q or f.page > last_q

        if f.markers > 0:
            f.verdict, f.ad_score = "CONTENT", 0
            f.reason = f"{f.markers} content marker(s)"
            continue

        score, why = 0, []
        if f.in_ad_cluster:
            score += 3
            why.append(f"repeated template x{f.fp_cluster_size}")
        if f.links > 0:
            score += 2
            why.append(f"hyperlink x{f.links}")
        if f.ad_keywords > 0:
            score += 2
            why.append(f"ad keyword x{f.ad_keywords}")
        if f.draw_ops >= prof.draw_ops_high:
            score += 1
            why.append(f"draw_ops={f.draw_ops}")
        if prof.watermark_frac and not f.has_watermark:
            score += 1
            why.append("no template watermark")
        # A1 -- colour. Deliberately NOT a veto: a content page carrying a big
        # coloured figure exists even though none appeared in the sample, so
        # this adds to a score that still needs corroboration to reach
        # ad_score_delete. Rule 1 already protects any page with a marker.
        colour_cut = prof.colour_threshold()
        if colour_cut is not None and f.colour_frac > colour_cut:
            score += prof.colour_score
            why.append(f"coloured {f.colour_frac:.1%} (content baseline "
                       f"{prof.content_colour_p95:.2%})")
        if f.outside_question_range:
            score += 1
            why.append("outside question range (front/back matter)")

        f.ad_score = score
        if score >= prof.ad_score_delete:
            f.verdict = "AD"
            f.reason = "no markers; " + ", ".join(why)
        elif score == 0:
            f.verdict = "CONTENT"
            f.reason = "no markers but no ad evidence -> solution continuation"
        else:
            f.verdict = "REVIEW"
            f.reason = f"ambiguous (score {score}): " + ", ".join(why)

    for i, f in enumerate(feats):
        if f.verdict == "CONTENT" and f.markers == 0 and f.ad_score == 0 and i > 0:
            if feats[i - 1].verdict == "AD":
                f.verdict = "REVIEW"
                f.reason = "markerless page following an ad page"
    return feats


def ordered_events(text: str, prof: Profile) -> list[tuple[str, str]]:
    """
    ("paper"|"subject"|"question", value) in text-layer reading order.

    Must be positional, not per-page: section boundaries land mid-page. Page
    41 of the 2018 file reads "Q. 30 ... Chemistry ... Q. 1", so a per-page
    pass relabels the last question of Physics as Chemistry.
    """
    events: list[tuple[int, str, str]] = []
    for pat in PAPER_PATTERNS:
        for m in re.finditer(pat, text):
            cand = " ".join(m.group(1).split())
            if cand.lower() not in SUBJECT_CANON:
                events.append((m.start(), "paper", cand))
    for m in SUBJECT_HEADER.finditer(text):
        events.append((m.start(), "subject", SUBJECT_CANON[m.group(1).lower()]))
    for m in prof.question_num().finditer(text):
        events.append((m.start(), "question", m.group(1)))
    events.sort(key=lambda e: e[0])
    # de-duplicate paper headers matched by more than one pattern at once
    out: list[tuple[str, str]] = []
    for _, kind, val in events:
        if out and out[-1] == (kind, val) and kind == "paper":
            continue
        out.append((kind, val))
    return out


def kept_pages(feats: list[PageFeatures]) -> list[int]:
    return [f.page for f in feats if f.verdict in ("CONTENT", "REVIEW")]


def ad_pages(feats: list[PageFeatures]) -> list[int]:
    return [f.page for f in feats if f.verdict == "AD"]
