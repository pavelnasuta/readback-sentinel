# Install Readback Sentinel

The bootstrap reports prerequisites first, then runs only the non-elevated commands you approve. It does not run `sudo`, UAC, package-manager elevation, or manual-action text.

## Quick start

From a fresh clone on macOS, Ubuntu/Debian, or Windows:

```bash
python3 scripts/bootstrap.py check --json
python3 scripts/bootstrap.py install --yes --skip-model --json
python3 scripts/bootstrap.py install --yes --model small --json
```

Start with `check`. It is read-only and its JSON receipt identifies whether Python 3.11+, `uv`, `ffmpeg`, the project configuration, and the lockfile are ready. The second command installs the locked Python environment without requesting a model. The third command explicitly consents to preparing the local `small` faster-whisper model after dependencies synchronize.

## `ACTION_REQUIRED`

`ACTION_REQUIRED` is a safe stop, not a partial success. Read the receipt's missing component or manual action, complete that system prerequisite yourself, then rerun `check`.

- **macOS:** install Homebrew manually if needed, then run `brew install ffmpeg` in your own terminal.
- **Ubuntu/Debian:** run `sudo apt-get update && sudo apt-get install -y ffmpeg` yourself. The bootstrap never runs `sudo`.
- **Windows:** install FFmpeg manually with your organization-approved package source; for winget environments, use the matching `winget` package command in an elevated terminal only when Windows asks you to approve it.

The bootstrap may run non-elevated `uv` commands only after an `install` confirmation or `--yes`. Re-read the next receipt after each manual repair.

## Model consent and cache

No model downloads during `check`, and `install --skip-model` never plans a model command. A model is prepared only when you pass one of `--model tiny`, `--model base`, or `--model small`; this can download model files into the local cache. Use `--model-cache PATH` to choose a cache directory explicitly:

```bash
python3 scripts/bootstrap.py install --yes --model small --model-cache .bootstrap-model-cache --json
```

The default cache is local to the current user: `~/Library/Caches/readback-sentinel/models` on macOS, `%LOCALAPPDATA%/readback-sentinel/models` on Windows, or `~/.cache/readback-sentinel/models` on Linux. On POSIX, the helper creates the cache owner-only.

## JSON receipts and privacy

With `--json`, the command writes exactly one JSON receipt to standard output and no progress prose. To retain a local copy, provide `--receipt PATH`:

```bash
python3 scripts/bootstrap.py check --json --receipt bootstrap-receipt-check.json
```

Bootstrap receipts describe prerequisite state and command results; they do not retain command stdout or stderr. The bootstrap does not upload anything and does not accept, inspect, or send customer audio, source text, transcripts, glossaries, or QC reports. Keep those materials outside Git and run the actual QC workflow only after setup is complete.

## Verify the development environment

After a successful dependency sync, run the synthetic suite:

```bash
uv run pytest -q
```

For the local analysis workflow and its human-review boundary, see [README.md](README.md).
