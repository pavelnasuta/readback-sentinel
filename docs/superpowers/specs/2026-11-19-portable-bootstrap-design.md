# Portable bootstrap design

**Status:** approved design; implementation not started
**Target release:** v0.5.0
**Repository:** Readback Sentinel
**Decision:** 2026-11-19

## Goal

Make a fresh clone usable through one local, non-Hermes-specific bootstrap command. It must diagnose prerequisites, apply explicitly approved installations on macOS, Ubuntu/Debian and Windows, optionally prepare a local faster-whisper model, and leave a machine-readable receipt for an agent or human.

## Product boundary

The bootstrap prepares a computer to run the existing local CLI. It is not an audio analysis job, server, queue, uploader, account system or quality evaluator.

It must never read, copy, upload, hash or list customer audio, books, transcripts, glossaries or previous QC results.

## User journeys

### Safe diagnostic (default)

```bash
python3 scripts/bootstrap.py check
python3 scripts/bootstrap.py check --json
```

`check` makes no persistent changes. It reports OS, architecture, Python version, `pip`, `uv`, `ffmpeg`, repository lockfile, available disk space and optional model-cache status.

Exit values:

| Exit | Meaning |
|---:|---|
| `0` | Environment is ready for the selected scope. |
| `2` | A recoverable prerequisite is missing or needs a human system-level action. |
| `1` | Bootstrap itself could not complete a safe check. |
| `64` | Invalid command-line arguments. |

### Interactive installation

```bash
python3 scripts/bootstrap.py install --model small
```

The script displays an ordered plan and asks for confirmation before every network download or package-manager command. The selected `small` model is an explicit request to download a model if no compatible local cache is found.

### Agent / unattended installation

```bash
python3 scripts/bootstrap.py install --yes --model small --json
```

`--yes` accepts only steps that do not require elevation. It does not invoke `sudo`, UAC, `doas`, `pkexec` or any equivalent escalation mechanism. A missing elevated dependency is reported as `MANUAL_ACTION_REQUIRED`, with one copyable command and a non-zero recoverable exit code.

### Optional model-free setup

```bash
python3 scripts/bootstrap.py install --yes --skip-model --json
```

This creates the Python environment and verifies synthetic tests and CLI help without downloading speech-model weights.

## Architecture

The bootstrap code must use the Python standard library only and must live outside `audiobook_qc/`. It has to run before project dependencies are installed.

```text
scripts/bootstrap.py              # small executable entry point
readback_bootstrap/
├── __init__.py
├── cli.py                         # argparse and exit mapping
├── checks.py                      # non-mutating prerequisite discovery
├── platforms.py                   # macOS / Ubuntu-Debian / Windows commands
├── plan.py                        # immutable install-step planning
├── apply.py                       # confirmation, subprocess execution, receipts
├── model.py                       # invokes prepared post-dependency model action
└── receipt.py                     # JSON-safe result schemas and redaction
scripts/prepare_model.py           # imports faster-whisper only after uv sync
tests/test_bootstrap_*.py          # synthetic, fully mocked contract tests
INSTALL.md                         # human and non-Hermes agent guide
```

`readback_bootstrap` may import only the Python standard library. The existing QC package must not import bootstrap code.

### Bootstrap phases

1. **Discover:** determine platform and inspect prerequisites without changing state.
2. **Plan:** create named steps, their commands, expected effects and whether each requires network or elevation.
3. **Consent:** print the plan; request confirmation unless `--yes` was passed.
4. **Apply:** execute only approved, non-elevated steps; stop safely on a failed command.
5. **Prepare model:** only after dependencies are ready and only with explicit `--model NAME`.
6. **Smoke test:** run `uv run pytest -q` and `uv run python scripts/run_qc.py --help`; do not analyse media.
7. **Receipt:** emit one JSON object to stdout for `--json`, and optionally write the same redacted object with `--receipt PATH`.

## Platform contract

| Platform | Detection | `ffmpeg` plan | Unsupported condition |
|---|---|---|---|
| macOS | `platform.system() == "Darwin"` | `brew install ffmpeg` when Homebrew exists | Without Homebrew: `MANUAL_ACTION_REQUIRED`, link to Homebrew and no shell-pipe installer. |
| Ubuntu/Debian | Linux plus `/etc/debian_version` | `apt-get install -y ffmpeg` only if already root | Non-root: print `sudo apt-get update && sudo apt-get install -y ffmpeg`; do not call `sudo`. |
| Windows | `platform.system() == "Windows"` | `winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements` | Missing `winget`: `MANUAL_ACTION_REQUIRED` with Microsoft package-manager instructions. |
| Other Linux / other OS | detected explicitly | no package command | `UNSUPPORTED_PLATFORM`; environment check remains usable. |

The script uses `sys.executable -m pip install --user uv` only after the user has approved a network install. If `pip` is unavailable, it reports a manual action instead of downloading or executing an arbitrary installer script.

For project dependencies, the only allowed command is:

```bash
uv sync --dev --locked
```

No bootstrap path modifies `pyproject.toml`, `uv.lock`, policy thresholds or QC code.

## Model preparation contract

`--model NAME` is mandatory to permit model preparation. The initial allowed values are `tiny`, `base` and `small`; no aliases such as `latest` are accepted.

After `uv sync`, bootstrap runs:

```bash
uv run python scripts/prepare_model.py --model NAME --cache-dir PATH
```

`prepare_model.py` loads the requested faster-whisper model into the selected local cache and returns a JSON receipt containing only:

- requested model name;
- cache directory path chosen by the operator;
- success/failure status;
- tool version and elapsed time;
- exception class and safe message when preparation fails.

It must not print access tokens, environment values, request URLs, source audio paths or model file listings. It does not accept arbitrary model repositories or revisions in v0.5.0.

Default cache location is platform-appropriate under the current user’s cache directory. `--model-cache PATH` overrides it. The path is created owner-only where the platform supports POSIX permissions.

## Receipt schema

A receipt is a JSON object, never a log transcript. Required keys:

```json
{
  "schema_version": 1,
  "command": "check | install",
  "status": "READY | ACTION_REQUIRED | FAILED | UNSUPPORTED_PLATFORM",
  "platform": {"system": "", "release": "", "machine": ""},
  "python": {"version": "", "supported": true},
  "components": [
    {"name": "uv", "status": "READY | MISSING | INSTALLED | MANUAL_ACTION_REQUIRED"}
  ],
  "steps": [
    {"id": "install_ffmpeg", "status": "SKIPPED | COMPLETED | FAILED | MANUAL_ACTION_REQUIRED", "requires_network": false, "requires_elevation": false}
  ],
  "smoke_test": {"status": "NOT_RUN | PASSED | FAILED"},
  "next_action": ""
}
```

Receipts must not contain secrets, complete environment variables, customer paths, audio metadata, book titles, model file names or subprocess output other than a bounded, redacted failure summary.

## Agent protocol

Any agent, including Hermes, uses the same protocol:

1. Clone or otherwise obtain the reviewed repository source.
2. Run `python3 scripts/bootstrap.py check --json`.
3. Present `ACTION_REQUIRED` items and request permission before running `install` or a model download.
4. Run `install --yes --skip-model --json` only after permission.
5. Run `install --yes --model small --json` only after separate permission for the model download.
6. Read the receipt and state what succeeded, what needs a human action and what was not tested.

`SKILL.md` must reference this protocol; it must not include a second installer implementation or infer that setup passed from command exit alone.

## Documentation and public positioning

- Add `INSTALL.md` with command examples for a human, any coding agent and Hermes.
- Rewrite README quick start to begin with `check`, then use `install`.
- Add a short subsection explaining that network downloads and elevated commands always require a separate user decision.
- Update `SKILL.md` to call `scripts/bootstrap.py check --json` before dependency/model work.
- Add `docs/releases/v0.5.0.md` and a `CHANGELOG.md` entry.
- Keep current boundaries: local processing, human review, no automatic narrator verdicts, no customer data in Git.

## Test and CI contract

All tests use mocks, temporary directories and synthetic process responses. CI must not install `ffmpeg`, invoke `brew`/`apt`/`winget`, call `pip` for bootstrap itself or download model weights.

Required test coverage:

1. `check` does not call a process-changing command.
2. Each supported OS produces the expected `ffmpeg` plan.
3. A non-root Ubuntu/Debian environment is `MANUAL_ACTION_REQUIRED`; no `sudo` subprocess is launched.
4. `--yes` skips elevated steps and records why.
5. Missing Homebrew, `winget` or `pip` produces a clear manual action, not a guessed installer.
6. `--skip-model` never invokes `prepare_model.py`.
7. `--model small` plans model preparation only after dependency setup is successful.
8. A failed external command stops later steps and returns a redacted receipt.
9. JSON receipt contains required fields and no command output/secret-like keys.
10. The existing QC regression suite continues to pass.

GitHub Actions becomes a synthetic matrix on `macos-latest`, `ubuntu-latest` and `windows-latest`, Python 3.11. The matrix proves bootstrap behavior and cross-platform Python compatibility; it does not certify package-manager availability, model performance or real-media analysis on those hosts.

## Explicit non-goals for v0.5.0

- installing Python itself;
- automatic elevation or password entry;
- Docker, server deployment, queue, GUI or dashboard;
- downloading client files or running a real analysis;
- model pinning/revision management beyond the explicit allowed model names;
- reference-narration comparison;
- claims about natural-audio accuracy or production readiness.

## Acceptance criteria

1. A fresh user with Python 3.11+ can run `python3 scripts/bootstrap.py check --json` without Hermes or project dependencies.
2. macOS, Ubuntu/Debian and Windows produce deterministic, platform-specific prerequisite plans.
3. No install or model download happens in default diagnostic mode.
4. Explicit setup produces a receipt that an arbitrary agent can consume without parsing terminal prose.
5. The bootstrap never uses privileges silently and never handles client content.
6. Synthetic tests pass locally and on the three-OS GitHub Actions matrix.
7. README, INSTALL guide, `SKILL.md`, changelog and v0.5.0 release note describe exactly the delivered behavior and its limitations.

## Rollback

The bootstrap is additive. Revert the v0.5.0 commit or restore the v0.4.1 tag. It does not alter existing QC reports or customer input data. Locally installed dependencies, Homebrew/apt/winget packages and downloaded model caches remain operator-managed and are not automatically removed by rollback.
