"""Redacted JSON receipt serialization."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from .models import Receipt


def write_receipt(receipt: Receipt, path: Path | None) -> str:
    """Return compact UTF-8 JSON and atomically replace an optional receipt file."""
    text = json.dumps(receipt.to_dict(), ensure_ascii=False, separators=(",", ":"))
    if path is None:
        return text

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary_path = Path(temporary.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return text
