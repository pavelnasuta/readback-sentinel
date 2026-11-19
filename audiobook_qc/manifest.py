"""Private-data-safe manifests and review-snapshot bindings."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_record(path: str | Path, role: str) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    return {
        "role": role,
        "filename": source.name,
        "size_bytes": source.stat().st_size,
        "sha256": _sha256(source),
    }


def new_run_id() -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


def _json_native(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _json_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_native(item) for item in value]
    return value


def ensure_unique_flag_ids(flags: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Materialize flags and reject missing or duplicate IDs before binding/export."""
    materialized = [dict(flag) for flag in flags]
    raw_ids = [flag.get("id") for flag in materialized]
    if any(not isinstance(flag_id, str) or not flag_id for flag_id in raw_ids):
        raise ValueError("Every flag must have a non-empty string ID.")
    ids = [str(flag_id) for flag_id in raw_ids]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for flag_id in ids:
        if flag_id in seen:
            duplicates.add(flag_id)
        seen.add(flag_id)
    if duplicates:
        raise ValueError(f"Duplicate flag IDs: {', '.join(sorted(duplicates))}")
    return materialized


def build_review_binding(
    *,
    inputs: Iterable[dict[str, Any]],
    config_hashes: dict[str, str],
    model: str,
    language: str,
    flags: Iterable[dict[str, Any]],
) -> str:
    """Hash the exact machine-review snapshot, excluding volatile run metadata."""
    unique_flags = ensure_unique_flag_ids(flags)
    payload = {
        "schema_version": 1,
        "inputs": sorted(
            [
                {
                    "role": item.get("role"),
                    "size_bytes": item.get("size_bytes"),
                    "sha256": item.get("sha256"),
                }
                for item in inputs
            ],
            key=lambda item: (str(item["role"]), str(item["sha256"])),
        ),
        "config_hashes": dict(sorted(config_hashes.items())),
        "model": model,
        "language": language,
        "flags": sorted(
            [_json_native(flag) for flag in unique_flags],
            key=lambda flag: str(flag.get("id", "")),
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_confirmed_flag_ids(
    path: str | Path,
    available_flags: Iterable[dict[str, Any]],
    expected_binding: str,
) -> set[str]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("review_binding"), str):
        raise ValueError("Confirmation must include review_binding from the reviewed report.")
    if payload["review_binding"] != expected_binding:
        raise ValueError("Confirmation belongs to a different review snapshot.")
    ids = payload.get("confirmed_flag_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        raise ValueError("Confirmation must contain a string list in confirmed_flag_ids.")
    if len(ids) != len(set(ids)):
        raise ValueError("confirmed_flag_ids must not contain duplicates.")
    available = {flag["id"] for flag in ensure_unique_flag_ids(available_flags)}
    unknown = sorted(set(ids) - available)
    if unknown:
        raise ValueError(f"Unknown confirmed flag IDs: {', '.join(unknown)}")
    return set(ids)


@dataclass
class RunManifest:
    tag: str
    tool_version: str
    model: str
    config_versions: dict[str, int]
    inputs: list[dict[str, Any]]
    run_id: str = field(default_factory=new_run_id)
    language: str = "ru"
    mode: str = "analysis"
    config_hashes: dict[str, str] = field(default_factory=dict)
    started_at: str = field(default_factory=_utc_now)
    finished_at: str | None = None
    status: str = "STARTED"
    artifacts: list[str] = field(default_factory=list)
    error: dict[str, str] | None = None

    @classmethod
    def start(cls, **kwargs: Any) -> RunManifest:
        return cls(**kwargs)

    def finish(
        self,
        status: str,
        artifacts: list[str] | None = None,
        error: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.finished_at = _utc_now()
        self.artifacts = artifacts or []
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "run_id": self.run_id,
            "tag": self.tag,
            "tool_version": self.tool_version,
            "model": self.model,
            "language": self.language,
            "mode": self.mode,
            "config_versions": self.config_versions,
            "config_hashes": self.config_hashes,
            "inputs": self.inputs,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "status": self.status,
            "artifacts": self.artifacts,
            "error": self.error,
        }

    def write(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        temporary.replace(destination)
        return destination
