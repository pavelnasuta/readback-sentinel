#!/usr/bin/env python3
"""Explicit, narrow local faster-whisper model preparation helper."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Sequence


_ALLOWED_MODELS = ("tiny", "base", "small")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prepare_model.py")
    parser.add_argument("--model", required=True, choices=_ALLOWED_MODELS)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--json", action="store_true")
    try:
        arguments = parser.parse_args(argv)
    except SystemExit:
        return 64

    cache_dir = Path(arguments.cache_dir)
    try:
        cache_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name == "posix":
            cache_dir.chmod(0o700)
        from faster_whisper import WhisperModel

        WhisperModel(arguments.model, download_root=str(cache_dir))
    except Exception:
        _emit({"model": arguments.model, "status": "FAILED"}, json_mode=arguments.json)
        return 1

    _emit({"model": arguments.model, "status": "READY"}, json_mode=arguments.json)
    return 0


def _emit(payload: dict[str, str], *, json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(payload["status"])


if __name__ == "__main__":
    raise SystemExit(main())
