"""Conservative text-difference and glossary candidate analysis."""
from __future__ import annotations

import json
from collections.abc import Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .normalize import normalize_for_alignment


def _segment_token_ranges(segments: Sequence[dict[str, Any]]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for segment in segments:
        size = len(normalize_for_alignment(str(segment.get("text", ""))).split())
        ranges.append((cursor, cursor + size))
        cursor += size
    return ranges


def _evidence_for_range(
    a1: int,
    a2: int,
    segments: Sequence[dict[str, Any]],
    ranges: Sequence[tuple[int, int]],
) -> tuple[float, float, float]:
    if not segments:
        return 0.0, 0.0, 0.0
    if a1 == a2:
        if a1 <= 0:
            point = float(segments[0].get("start", 0.0))
            confidence = float(segments[0].get("avg_logprob", -0.5))
            return point, point, confidence
        index = min(a1 - 1, ranges[-1][1] - 1)
        for segment, (start, end) in zip(segments, ranges):
            if start <= index < end:
                point = float(segment.get("end", segment.get("start", 0.0)))
                return point, point, float(segment.get("avg_logprob", -0.5))
        point = float(segments[-1].get("end", 0.0))
        return point, point, float(segments[-1].get("avg_logprob", -0.5))

    affected = [
        segment
        for segment, (start, end) in zip(segments, ranges)
        if start < a2 and a1 < end
    ]
    if not affected:
        return 0.0, 0.0, 0.0
    return (
        float(affected[0].get("start", 0.0)),
        float(affected[-1].get("end", affected[-1].get("start", 0.0))),
        min(float(segment.get("avg_logprob", -0.5)) for segment in affected),
    )


def classify_diff(
    recognized_text: str,
    printed_text: str,
    segments: Sequence[dict[str, Any]],
    thresholds: Any,
) -> list[dict[str, Any]]:
    """Return review candidates; text/ASR heuristics never prove narrator error."""
    del thresholds  # Retained for the versioned public API; not used as a verdict gate.
    recognized_tokens = normalize_for_alignment(recognized_text).split()
    printed_tokens = normalize_for_alignment(printed_text).split()
    opcodes = SequenceMatcher(None, recognized_tokens, printed_tokens, autojunk=False).get_opcodes()
    ranges = _segment_token_ranges(segments)
    flags: list[dict[str, Any]] = []

    for tag, a1, a2, b1, b2 in opcodes:
        if tag == "equal":
            continue
        heard = recognized_tokens[a1:a2]
        expected = printed_tokens[b1:b2]
        start, end, confidence = _evidence_for_range(a1, a2, segments, ranges)
        if tag == "replace":
            category = "WORD_SUBSTITUTION"
            description = f"Кандидат на замену: '{' '.join(expected)}' → '{' '.join(heard)}'"
        elif tag == "insert":
            category = "MISSING_TEXT"
            description = f"Кандидат на пропуск: '{' '.join(expected)[:120]}'"
        elif tag == "delete":
            category = "ADDED_TEXT"
            description = f"Кандидат на вставку: '{' '.join(heard)[:120]}'"
        else:
            continue
        flags.append({
            "id": f"flag_{len(flags):04d}",
            "timestamp": round(start, 2),
            "timestamp_end": round(end, 2),
            "severity": "REVIEW",
            "category": category,
            "description": description,
            "audio_heard": " ".join(heard)[:200],
            "book_reference": " ".join(expected)[:200],
            "asr_confidence": round(confidence, 3),
            "action": "Проверить фрагмент на слух; автоматический вывод не является заданием диктору.",
        })
    return flags


def _near_single_token(token: str, expected: str) -> bool:
    if len(token) != len(expected) or not token or token[0] != expected[0]:
        return False
    return SequenceMatcher(None, token, expected).ratio() >= 0.75


def check_glossary(
    glossary_path: str | None,
    segments: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find localized near-spellings; this is not pronunciation recognition."""
    if not glossary_path:
        return []
    path = Path(glossary_path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Glossary must be a JSON list.")

    flags: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict) or not isinstance(entry.get("term"), str):
            raise ValueError("Each glossary entry must contain a string 'term'.")
        variants = [
            normalize_for_alignment(value).split()
            for value in (entry["term"], entry.get("transcription"))
            if isinstance(value, str) and normalize_for_alignment(value)
        ]
        if not variants:
            continue
        for segment in segments:
            tokens = normalize_for_alignment(str(segment.get("text", ""))).split()
            flagged_spans: set[tuple[int, int]] = set()
            for start in range(len(tokens)):
                exact_span = next(
                    (
                        (start, start + len(variant))
                        for variant in variants
                        if tokens[start:start + len(variant)] == variant
                    ),
                    None,
                )
                if exact_span is not None:
                    continue
                expected = next(
                    (
                        variant
                        for variant in variants
                        if start + len(variant) <= len(tokens)
                        and all(
                            actual == target or _near_single_token(actual, target)
                            for actual, target in zip(tokens[start:start + len(variant)], variant)
                        )
                        and tokens[start:start + len(variant)] != variant
                    ),
                    None,
                )
                if expected is None:
                    continue
                span = (start, start + len(expected))
                if span in flagged_spans:
                    continue
                flagged_spans.add(span)
                heard = " ".join(tokens[span[0]:span[1]])
                flags.append({
                    "id": f"gloss_{9000 + len(flags):04d}",
                    "timestamp": round(float(segment.get("start", 0.0)), 2),
                    "timestamp_end": round(float(segment.get("end", 0.0)), 2),
                    "severity": "REVIEW",
                    "category": "GLOSSARY_REVIEW",
                    "description": f"Написание рядом с термином '{entry['term']}' требует проверки по аудио: '{heard}'",
                    "audio_heard": heard,
                    "book_reference": entry.get("transcription", entry["term"]),
                    "asr_confidence": round(float(segment.get("avg_logprob", -0.5)), 3),
                    "action": f"Проверить термин на слух: {entry['term']}",
                })
    return flags
