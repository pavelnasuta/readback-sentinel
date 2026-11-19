#!/usr/bin/env python3
"""Local audiobook QC: preliminary analysis plus bound confirmation export."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pymupdf as fitz
from docx import Document as DocxDocument

from audiobook_qc import __version__
from audiobook_qc.acoustics import analyze_acoustics, build_acoustic_flags
from audiobook_qc.alignment import align_fragment
from audiobook_qc.asr import save_transcript, transcribe
from audiobook_qc.config import ConfigError, RuntimeConfig, load_runtime_config
from audiobook_qc.exports import (
    save_prosody_figure,
    write_confirmed_docx,
    write_marker_csv,
    write_pdf,
    write_triage_docx,
)
from audiobook_qc.manifest import (
    RunManifest,
    build_review_binding,
    input_record,
    new_run_id,
)
from audiobook_qc.manifest import (
    load_confirmed_flag_ids as _load_confirmed_flag_ids,
)
from audiobook_qc.normalize import normalize_for_alignment
from audiobook_qc.prosody import analyze_prosody
from audiobook_qc.text_analysis import check_glossary as analyze_glossary
from audiobook_qc.text_analysis import classify_diff as analyze_text_diff


def normalize(text: str) -> str:
    return normalize_for_alignment(text)


def find_subseq(haystack: list[str], needle: list[str]) -> int:
    size = len(needle)
    for index in range(len(haystack) - size + 1):
        if haystack[index:index + size] == needle:
            return index
    return -1


def locate_fragment(printed_tokens: list[str], rec_tokens: list[str]):
    result = align_fragment(" ".join(printed_tokens), " ".join(rec_tokens), (15, 12, 10, 8, 6))
    if result.status != "ALIGNED":
        return None
    return {
        "anchor_tokens": result.anchor_tokens,
        "start": result.printed_start,
        "end": result.printed_end,
        "audio_offset": result.audio_offset,
        "window": list(result.window_tokens),
    }


def load_confirmed_flag_ids(path: str | Path, available_flags, expected_binding: str | None = None) -> set[str]:
    if expected_binding is None:
        raise ValueError("review_binding is required; confirmations cannot be accepted by ordinal IDs alone.")
    if isinstance(available_flags, set):
        available_flags = [{"id": item} for item in available_flags]
    return _load_confirmed_flag_ids(path, available_flags, expected_binding)


def json_default(value: Any):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def extract_text(path: str | Path) -> str:
    source = Path(path).expanduser()
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        with fitz.open(source) as document:
            return "\n".join(page.get_text() for page in document)
    if suffix == ".docx":
        document = DocxDocument(str(source))
        blocks = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
        blocks.extend(
            cell.text
            for table in document.tables
            for row in table.rows
            for cell in row.cells
            if cell.text
        )
        return "\n".join(str(block) for block in blocks)
    if suffix == ".txt":
        return source.read_text(encoding="utf-8")
    raise ValueError(
        f"Unsupported printed-text format: {source.suffix or '(no extension)'}. Use PDF, DOCX, or TXT."
    )


def _safe_tag(value: str) -> str:
    if not value or not re.fullmatch(r"[\w.-]+", value, flags=re.UNICODE):
        raise ValueError("--tag may contain only letters, digits, underscore, dot, and hyphen.")
    return value


def _config_hashes(runtime: RuntimeConfig) -> dict[str, str]:
    return {
        "thresholds": input_record(runtime.thresholds_path, "threshold_config")["sha256"],
        "policy": input_record(runtime.policy_path, "policy_config")["sha256"],
    }


def _input_records(args: argparse.Namespace) -> list[dict[str, Any]]:
    records = [input_record(args.audio, "audio"), input_record(args.text, "printed_text")]
    if args.glossary:
        records.append(input_record(args.glossary, "glossary"))
    return records


def _new_run_directory(root: Path, tag: str, run_id: str, mode: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    directory = root / f"{mode}_{tag}_{run_id}"
    directory.mkdir(exist_ok=False)
    return directory


def _artifact_names(run_dir: Path, manifest_path: Path) -> list[str]:
    return sorted(path.name for path in run_dir.iterdir() if path.is_file() and path != manifest_path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=json_default, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _alignment_failure() -> dict[str, Any]:
    return {
        "id": "alignment_0001",
        "timestamp": 0.0,
        "timestamp_end": 0.0,
        "severity": "REVIEW",
        "category": "ALIGNMENT_FAILED",
        "description": "Фрагмент не удалось надёжно привязать к печатному тексту; расхождения не вычислялись.",
        "audio_heard": "",
        "book_reference": "",
        "asr_confidence": 0.0,
        "action": "Проверить соответствие аудио и текста, затем повторить анализ.",
    }


def _run_analysis(
    args: argparse.Namespace,
    runtime: RuntimeConfig,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    print(f"=== AI QC ANALYSIS === run={manifest.run_id} tag={args.tag}", flush=True)
    printed_raw = extract_text(args.text)
    printed_tokens = normalize(printed_raw).split()

    segments, recognized_lines = transcribe(args.audio, args.model, args.language)
    recognized_text = "\n".join(recognized_lines)
    recognized_tokens = normalize(recognized_text).split()
    transcript_paths = save_transcript(segments, str(run_dir), args.tag)

    result = align_fragment(printed_raw, recognized_text, runtime.thresholds.text.anchor_token_attempts)
    alignment = {
        "status": result.status,
        "level": result.level,
        "confidence": result.confidence,
        "anchor_tokens": result.anchor_tokens,
        "printed_start": result.printed_start,
        "printed_end": result.printed_end,
        "reason": result.reason,
    }
    if result.status != "ALIGNED":
        matched_pct = None
        diff_flags = [_alignment_failure()]
    else:
        matched_pct = result.matched_pct
        diff_flags = analyze_text_diff(
            recognized_text,
            " ".join(result.window_tokens),
            segments,
            runtime.thresholds.text,
        )

    glossary_flags = analyze_glossary(args.glossary, segments)
    acoustics = analyze_acoustics(args.audio, runtime.thresholds.audio.clip_sample_amplitude)
    acoustic_flags = build_acoustic_flags(acoustics, runtime)
    prosody = analyze_prosody(args.audio, runtime.thresholds.prosody)
    prosody_flags: list[dict[str, Any]] = []
    if prosody.get("monotony_flag"):
        prosody_flags.append({
            "id": "prosody_0000",
            "timestamp": 0.0,
            "timestamp_end": round(float(acoustics["duration"]), 2),
            "severity": "REVIEW",
            "category": "MONOTONY_CANDIDATE",
            "description": "Диагностический устойчивый диапазон F0 ниже настроенного порога.",
            "audio_heard": "",
            "book_reference": "",
            "asr_confidence": 0.0,
            "action": "Экспертно проверить интонацию; автоматическая метрика не является вердиктом.",
        })

    all_flags = diff_flags + glossary_flags + acoustic_flags + prosody_flags
    n_critical = sum(flag["severity"] == "CRITICAL" for flag in all_flags)
    n_review = sum(flag["severity"] == "REVIEW" for flag in all_flags)
    n_artifact = sum(flag["severity"] == "STT_ARTIFACT" for flag in all_flags)
    verdict_accuracy = (
        "сверка не выполнена"
        if matched_pct is None
        else "требуется ручная проверка кандидатов" if diff_flags
        else "автоматических кандидатов не сформировано"
    )
    verdict_noise = "не оценено: методика шума не откалибрована"
    verdict_clip = "есть кандидат" if acoustics["n_clipped"] else "превышений порога не обнаружено"
    verdict_pause = (
        "есть кандидат"
        if any(flag["category"] == "ANOMALOUS_PAUSE" for flag in acoustic_flags)
        else "кандидатов не сформировано"
    )
    verdict_intonation = (
        "не оценено: недостаточно данных"
        if prosody.get("status") in {"NOT_EVALUATED", "INSUFFICIENT_EVIDENCE"}
        else "есть кандидат REVIEW" if prosody.get("monotony_flag")
        else "только диагностические метрики"
    )
    config_hashes = manifest.config_hashes
    review_binding = build_review_binding(
        inputs=manifest.inputs,
        config_hashes=config_hashes,
        model=args.model,
        language=args.language,
        flags=all_flags,
    )
    report: dict[str, Any] = {
        "status": "PRELIMINARY_TRIAGE_REQUIRED",
        "schema_version": 3,
        "run_id": manifest.run_id,
        "review_binding": review_binding,
        "tool_version": __version__,
        "model": args.model,
        "language": args.language,
        "config_versions": manifest.config_versions,
        "config_hashes": config_hashes,
        "inputs": manifest.inputs,
        "file": Path(args.audio).name,
        "tag": args.tag,
        "audio_name": Path(args.audio).name,
        "duration": acoustics["duration"] / 60,
        "n_words": len(recognized_tokens),
        "n_printed_words": len(printed_tokens),
        "n_seg": len(segments),
        "alignment": alignment,
        "matched_pct": round(matched_pct, 1) if matched_pct is not None else None,
        "acoustics": {
            key: (round(value, 4) if isinstance(value, float) else value)
            for key, value in acoustics.items()
        },
        "prosody": {key: value for key, value in prosody.items() if not key.startswith("_")},
        "n_flags": len(all_flags),
        "n_critical": n_critical,
        "n_review": n_review,
        "n_artifact": n_artifact,
        "flags": all_flags,
        "verdict_accuracy": verdict_accuracy,
        "verdict_noise": verdict_noise,
        "verdict_clip": verdict_clip,
        "verdict_pause": verdict_pause,
        "verdict_intonation": verdict_intonation,
    }

    prosody_path = save_prosody_figure(
        args.audio,
        str(run_dir / f"prosody_{args.tag}.png"),
        analysis=prosody,
    )
    report_path = run_dir / f"report_{args.tag}.json"
    _write_json(report_path, report)
    triage_flags = [flag for flag in all_flags if flag["severity"] in ("CRITICAL", "REVIEW")]
    marker_path = write_marker_csv(triage_flags, str(run_dir), args.tag, "triage_markers")
    triage_path = write_triage_docx(triage_flags, str(run_dir), args.tag, Path(args.audio).name)
    pdf_path = write_pdf(report, str(run_dir), args.tag)
    manifest.finish(
        "COMPLETED",
        artifacts=[
            report_path.name,
            pdf_path.name,
            transcript_paths[0].name,
            transcript_paths[1].name,
            marker_path.name,
            triage_path.name,
            prosody_path.name,
        ],
    )


def _verify_report_inputs(report: dict[str, Any], current_inputs: Sequence[dict[str, Any]]) -> None:
    reviewed = {
        item.get("role"): item.get("sha256")
        for item in report.get("inputs", [])
        if item.get("role") in {"audio", "printed_text", "glossary"}
    }
    current = {item["role"]: item["sha256"] for item in current_inputs}
    if reviewed != current:
        raise ValueError("Current inputs do not match the reviewed report inputs.")


def _run_confirmation(
    args: argparse.Namespace,
    manifest: RunManifest,
    run_dir: Path,
) -> None:
    report_path = Path(args.review_report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict) or report.get("status") != "PRELIMINARY_TRIAGE_REQUIRED":
        raise ValueError("--review-report must be a preliminary QC report.")
    current_inputs = _input_records(args)
    _verify_report_inputs(report, current_inputs)
    flags = report.get("flags")
    if not isinstance(flags, list):
        raise ValueError("Reviewed report has no valid flags list.")
    expected_binding = report.get("review_binding")
    if not isinstance(expected_binding, str):
        raise ValueError("Reviewed report has no review_binding.")
    recomputed = build_review_binding(
        inputs=report.get("inputs", []),
        config_hashes=report.get("config_hashes", {}),
        model=str(report.get("model", "")),
        language=str(report.get("language", "")),
        flags=flags,
    )
    if recomputed != expected_binding:
        raise ValueError("Reviewed report content does not match its review_binding.")
    reviewable = [flag for flag in flags if flag.get("severity") in ("CRITICAL", "REVIEW")]
    confirmed_ids = load_confirmed_flag_ids(args.confirmed_flags, reviewable, expected_binding)
    confirmed = [flag for flag in reviewable if flag.get("id") in confirmed_ids]
    marker_path = write_marker_csv(confirmed, str(run_dir), args.tag, "audition_markers")
    docx_path = write_confirmed_docx(confirmed, str(run_dir), args.tag, Path(args.audio).name)
    manifest.finish("COMPLETED", artifacts=[docx_path.name, marker_path.name])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local audiobook QC candidate analysis")
    parser.add_argument("--audio", required=True, help="WAV/MP3/FLAC input")
    parser.add_argument("--text", required=True, help="Printed source PDF/DOCX/TXT")
    parser.add_argument("--glossary", default=None, help="Optional glossary.json")
    parser.add_argument("--reference", default=None, help="Reserved; demo comparison is not implemented")
    parser.add_argument("--confirmed-flags", default=None, help="Bound manual confirmation JSON")
    parser.add_argument("--review-report", default=None, help="Reviewed report JSON for confirmation-only export")
    parser.add_argument("--output", default=os.path.expanduser("~/Downloads/qc_output/"), help="Run root directory")
    parser.add_argument("--model", default="small", help="Whisper model")
    parser.add_argument("--tag", default="untagged", help="Safe run label")
    parser.add_argument("--language", default="ru", help="ASR language")
    parser.add_argument("--thresholds", default=str(PROJECT_ROOT / "configs" / "thresholds.yaml"))
    parser.add_argument("--policy", default=str(PROJECT_ROOT / "configs" / "policy.yaml"))
    return parser


def _prepare_args(argv: Sequence[str] | None) -> tuple[argparse.ArgumentParser, argparse.Namespace, RuntimeConfig]:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.reference:
        parser.error("--reference is reserved; demo comparison is not implemented in v0.4.0.")
    if bool(args.confirmed_flags) != bool(args.review_report):
        parser.error("--confirmed-flags and --review-report must be provided together.")
    try:
        args.tag = _safe_tag(args.tag)
    except ValueError as error:
        parser.error(str(error))
    for field in ("audio", "text", "glossary", "confirmed_flags", "review_report", "thresholds", "policy"):
        value = getattr(args, field)
        if value:
            setattr(args, field, str(Path(value).expanduser().resolve()))
    for label in ("audio", "text"):
        if not Path(getattr(args, label)).is_file():
            parser.error(f"--{label} file does not exist: {getattr(args, label)}")
    if args.glossary and not Path(args.glossary).is_file():
        parser.error(f"--glossary file does not exist: {args.glossary}")
    if args.confirmed_flags and not Path(args.confirmed_flags).is_file():
        parser.error(f"--confirmed-flags file does not exist: {args.confirmed_flags}")
    if args.review_report and not Path(args.review_report).is_file():
        parser.error(f"--review-report file does not exist: {args.review_report}")
    try:
        runtime = load_runtime_config(args.thresholds, args.policy)
    except ConfigError as error:
        parser.error(str(error))
    args.output = str(Path(args.output).expanduser().resolve())
    return parser, args, runtime


def main(argv: Sequence[str] | None = None) -> int:
    _parser, args, runtime = _prepare_args(argv)
    mode = "confirmation" if args.confirmed_flags else "analysis"
    run_id = new_run_id()
    run_dir = _new_run_directory(Path(args.output), args.tag, run_id, mode)
    inputs = _input_records(args)
    reviewed_metadata: dict[str, Any] = {}
    if mode == "confirmation":
        inputs.extend([
            input_record(args.review_report, "review_report"),
            input_record(args.confirmed_flags, "confirmation"),
        ])
        try:
            candidate = json.loads(Path(args.review_report).read_text(encoding="utf-8"))
            if isinstance(candidate, dict):
                reviewed_metadata = candidate
        except (OSError, UnicodeError, json.JSONDecodeError):
            # _run_confirmation records the precise validation error after the
            # STARTED manifest has been persisted.
            pass

    reviewed_model = reviewed_metadata.get("model")
    reviewed_language = reviewed_metadata.get("language")
    reviewed_versions = reviewed_metadata.get("config_versions")
    reviewed_hashes = reviewed_metadata.get("config_hashes")
    manifest = RunManifest.start(
        run_id=run_id,
        tag=args.tag,
        tool_version=__version__,
        model=reviewed_model if isinstance(reviewed_model, str) else args.model,
        language=reviewed_language if isinstance(reviewed_language, str) else args.language,
        mode=mode,
        config_versions=(
            reviewed_versions
            if isinstance(reviewed_versions, dict)
            else {"thresholds": runtime.thresholds.version, "policy": runtime.policy.version}
        ),
        config_hashes=reviewed_hashes if isinstance(reviewed_hashes, dict) else _config_hashes(runtime),
        inputs=inputs,
    )
    manifest_path = run_dir / f"run_manifest_{args.tag}.json"
    manifest.write(manifest_path)
    try:
        if mode == "confirmation":
            _run_confirmation(args, manifest, run_dir)
        else:
            _run_analysis(args, runtime, manifest, run_dir)
    except Exception as error:  # noqa: BLE001 - every runtime failure must persist FAILED state
        manifest.finish(
            "FAILED",
            artifacts=_artifact_names(run_dir, manifest_path),
            error={"type": type(error).__name__, "message": str(error)},
        )
        manifest.write(manifest_path)
        print(f"[FAILED] {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        print(f"[RUN] {run_dir}", file=sys.stderr, flush=True)
        return 1
    manifest.write(manifest_path)
    print(f"[COMPLETED] {run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
