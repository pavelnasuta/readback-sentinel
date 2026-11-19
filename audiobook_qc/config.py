"""Validated runtime configuration for the QC pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Configuration is missing, malformed, or violates the safety contract."""


@dataclass(frozen=True)
class TextThresholds:
    anchor_token_attempts: tuple[int, ...]


@dataclass(frozen=True)
class AudioThresholds:
    clip_sample_amplitude: float
    lead_or_trail_silence_review_s: float


@dataclass(frozen=True)
class ProsodyThresholds:
    monotony_range_semitones: float
    monotony_min_voiced_duration_s: float


@dataclass(frozen=True)
class Thresholds:
    version: int
    text: TextThresholds
    audio: AudioThresholds
    prosody: ProsodyThresholds


@dataclass(frozen=True)
class SafetyPolicy:
    candidate_not_verdict: bool
    human_triage_required_before_narrator_export: bool
    automatic_narrator_export: bool


@dataclass(frozen=True)
class RoutingPolicy:
    producer_pdf: str
    narrator_docx: str
    audition_csv: str
    diagnostic_json: str


@dataclass(frozen=True)
class Policy:
    version: int
    safety: SafetyPolicy
    routing: RoutingPolicy


@dataclass(frozen=True)
class RuntimeConfig:
    thresholds: Thresholds
    policy: Policy
    thresholds_path: Path
    policy_path: Path


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be a mapping")
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigError(f"Config file does not exist: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error
    return _mapping(payload, str(path))


def _required(mapping: dict[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ConfigError(f"Missing {label}.{key}")
    return mapping[key]


def load_runtime_config(thresholds_path: str | Path, policy_path: str | Path) -> RuntimeConfig:
    """Read repository YAML and reject any configuration that weakens triage safety."""
    thresholds_source = Path(thresholds_path).expanduser().resolve()
    policy_source = Path(policy_path).expanduser().resolve()
    thresholds_raw = _load_yaml(thresholds_source)
    policy_raw = _load_yaml(policy_source)

    text_raw = _mapping(_required(thresholds_raw, "text", "thresholds"), "thresholds.text")
    audio_raw = _mapping(_required(thresholds_raw, "audio", "thresholds"), "thresholds.audio")
    prosody_raw = _mapping(_required(thresholds_raw, "prosody", "thresholds"), "thresholds.prosody")
    attempts = tuple(int(value) for value in _required(text_raw, "anchor_token_attempts", "thresholds.text"))
    if not attempts or any(value < 3 for value in attempts):
        raise ConfigError("thresholds.text.anchor_token_attempts must contain integers >= 3")

    thresholds = Thresholds(
        version=int(_required(thresholds_raw, "version", "thresholds")),
        text=TextThresholds(anchor_token_attempts=attempts),
        audio=AudioThresholds(
            clip_sample_amplitude=float(_required(audio_raw, "clip_sample_amplitude", "thresholds.audio")),
            lead_or_trail_silence_review_s=float(_required(audio_raw, "lead_or_trail_silence_review_s", "thresholds.audio")),
        ),
        prosody=ProsodyThresholds(
            monotony_range_semitones=float(_required(prosody_raw, "monotony_range_semitones", "thresholds.prosody")),
            monotony_min_voiced_duration_s=float(_required(prosody_raw, "monotony_min_voiced_duration_s", "thresholds.prosody")),
        ),
    )

    safety_raw = _mapping(_required(policy_raw, "safety", "policy"), "policy.safety")
    routing_raw = _mapping(_required(policy_raw, "routing", "policy"), "policy.routing")
    policy = Policy(
        version=int(_required(policy_raw, "version", "policy")),
        safety=SafetyPolicy(
            candidate_not_verdict=bool(_required(safety_raw, "candidate_not_verdict", "policy.safety")),
            human_triage_required_before_narrator_export=bool(_required(safety_raw, "human_triage_required_before_narrator_export", "policy.safety")),
            automatic_narrator_export=bool(_required(safety_raw, "automatic_narrator_export", "policy.safety")),
        ),
        routing=RoutingPolicy(
            producer_pdf=str(_required(routing_raw, "producer_pdf", "policy.routing")),
            narrator_docx=str(_required(routing_raw, "narrator_docx", "policy.routing")),
            audition_csv=str(_required(routing_raw, "audition_csv", "policy.routing")),
            diagnostic_json=str(_required(routing_raw, "diagnostic_json", "policy.routing")),
        ),
    )
    if not policy.safety.candidate_not_verdict:
        raise ConfigError("policy.safety.candidate_not_verdict must remain true")
    if not policy.safety.human_triage_required_before_narrator_export:
        raise ConfigError("policy.safety.human_triage_required_before_narrator_export must remain true")
    if policy.safety.automatic_narrator_export:
        raise ConfigError("policy.safety.automatic_narrator_export must remain false")
    for field, value in (("narrator_docx", policy.routing.narrator_docx), ("audition_csv", policy.routing.audition_csv)):
        if value != "confirmed_only":
            raise ConfigError(f"policy.routing.{field} must be confirmed_only")
    return RuntimeConfig(thresholds, policy, thresholds_source, policy_source)
