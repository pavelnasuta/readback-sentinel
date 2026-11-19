"""Safe hierarchical alignment between a printed source and ASR transcript."""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from .normalize import alignment_tokens


@dataclass(frozen=True)
class AlignmentResult:
    status: str
    level: str | None
    confidence: float
    anchor_tokens: int | None
    printed_start: int | None
    printed_end: int | None
    audio_offset: int | None
    window_tokens: tuple[str, ...]
    window_text: str
    matched_pct: float | None
    reason: str | None = None


def _find_all_subsequences(haystack: tuple[str, ...], needle: tuple[str, ...]) -> list[int]:
    if not needle or len(needle) > len(haystack):
        return []
    return [
        index
        for index in range(len(haystack) - len(needle) + 1)
        if haystack[index:index + len(needle)] == needle
    ]


def _sentence_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        tokens = alignment_tokens(sentence)
        if tokens:
            ranges.append((cursor, cursor + len(tokens)))
            cursor += len(tokens)
    return ranges


def _level_for_range(start: int, end: int, sentence_ranges: list[tuple[int, int]]) -> str:
    for sentence_start, sentence_end in sentence_ranges:
        if sentence_start <= start and end <= sentence_end:
            return "sentence"
    return "document"


def _failed(reason: str) -> AlignmentResult:
    return AlignmentResult(
        status="ALIGNMENT_FAILED", level=None, confidence=0.0, anchor_tokens=None,
        printed_start=None, printed_end=None, audio_offset=None, window_tokens=(),
        window_text="", matched_pct=None, reason=reason,
    )


def align_fragment(printed_text: str, recognized_text: str, anchor_attempts: tuple[int, ...]) -> AlignmentResult:
    """Locate one unambiguous exact-boundary candidate and compare only it."""
    printed = alignment_tokens(printed_text)
    recognized = alignment_tokens(recognized_text)
    if not printed:
        return _failed("Printed text has no comparable tokens.")
    if not recognized:
        return _failed("ASR transcript has no comparable tokens.")

    ranges = _sentence_ranges(printed_text)
    saw_ambiguous = False
    for size in anchor_attempts:
        if size < 3 or len(recognized) < size:
            continue
        start_anchor = recognized[:size]
        end_anchor = recognized[-size:]
        starts = _find_all_subsequences(printed, start_anchor)
        ends = _find_all_subsequences(printed, end_anchor)
        candidates: list[tuple[float, int, int, tuple[str, ...]]] = []
        for start in starts:
            for end_start in ends:
                end = end_start + size
                if end_start < start or end - start < len(start_anchor):
                    continue
                candidate = printed[start:end]
                ratio = SequenceMatcher(None, recognized, candidate, autojunk=False).ratio()
                candidates.append((ratio, start, end, candidate))
        if not candidates:
            continue
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        best = candidates[0]
        equally_good = [item for item in candidates if abs(item[0] - best[0]) < 1e-12]
        if len(equally_good) > 1:
            saw_ambiguous = True
            continue
        ratio, start, end, candidate = best
        matched = sum(
            a2 - a1
            for opcode, a1, a2, _b1, _b2 in SequenceMatcher(None, recognized, candidate, autojunk=False).get_opcodes()
            if opcode == "equal"
        )
        return AlignmentResult(
            status="ALIGNED",
            level=_level_for_range(start, end, ranges),
            confidence=round(ratio, 3),
            anchor_tokens=size,
            printed_start=start,
            printed_end=end,
            audio_offset=0,
            window_tokens=candidate,
            window_text=" ".join(candidate),
            matched_pct=round(100 * matched / len(recognized), 1),
        )
    if saw_ambiguous:
        return _failed("Alignment is ambiguous: multiple equally good source fragments were found.")
    return _failed("No exact normalised start/end anchors were found.")
