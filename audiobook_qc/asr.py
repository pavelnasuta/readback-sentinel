"""ASR module: transcription and transcript persistence."""
from __future__ import annotations

import json
from pathlib import Path

from faster_whisper import WhisperModel


def transcribe(
    audio_path: str,
    model_name: str = "small",
    language: str = "ru",
) -> tuple[list[dict], list[str]]:
    """Transcribe audio with faster-whisper.

    Returns:
        segments: list of {start, end, text, avg_logprob}
        full_text: list of segment strings
    """
    print(f"[ASR] Loading model '{model_name}'...", flush=True)
    t0 = __import__("time").time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    print(f"[ASR] Loaded in {__import__('time').time()-t0:.1f}s", flush=True)

    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    print(f"[ASR] Language: {info.language} (prob={info.language_probability:.2f})", flush=True)

    seg_list: list[dict] = []
    full_text: list[str] = []
    for seg in segments:
        seg_list.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
            "avg_logprob": seg.avg_logprob if hasattr(seg, "avg_logprob") else -0.5,
        })
        full_text.append(seg.text.strip())

    print(f"[ASR] Done: {len(seg_list)} segments, {sum(len(s['text']) for s in seg_list)} chars", flush=True)
    return seg_list, full_text


def save_transcript(segments: list[dict], output_dir: str, tag: str) -> tuple[Path, Path]:
    """Persist plain-text and segment-JSON transcripts."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    full_text = "\n".join(s["text"] for s in segments)
    txt_path = out / f"transcript_{tag}.txt"
    json_path = out / f"transcript_segments_{tag}.json"
    txt_path.write_text(full_text, encoding="utf-8")
    json_path.write_text(json.dumps(segments, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[ASR] transcripts: {txt_path.name}, {json_path.name}", flush=True)
    return txt_path, json_path
