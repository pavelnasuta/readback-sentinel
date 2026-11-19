# Portable Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a standalone, safe, cross-platform bootstrap CLI so any human or coding agent can prepare Readback Sentinel without Hermes.

**Architecture:** `scripts/bootstrap.py` remains a dependency-free entry point and delegates to a new standard-library-only `readback_bootstrap` package. It discovers prerequisites, creates an immutable install plan, applies only explicitly authorised non-elevated commands, optionally prepares a requested local model after dependencies are ready, and emits a redacted JSON receipt. Existing QC modules remain untouched.

**Tech Stack:** Python 3.11 stdlib (`argparse`, `dataclasses`, `json`, `pathlib`, `platform`, `shutil`, `subprocess`, `sys`, `tempfile`), project `uv` environment, `faster-whisper` only in the post-install model helper, pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-19-portable-bootstrap-design.md`

## Global Constraints

- Bootstrap code imports only Python stdlib until `uv sync --dev --locked` completes.
- Default `check` is strictly non-mutating: no subprocess that changes state, no model import, no cache directory creation, no network request.
- Customer media, books, transcripts, glossaries, reports, hashes of those assets and credentials are out of scope and must never be read or logged.
- `--yes` never triggers privilege escalation; no `sudo`, `doas`, `pkexec`, PowerShell elevation, shell pipeline or arbitrary URL installer.
- Model acquisition happens only after explicit `--model tiny|base|small`; `--skip-model` wins over a default model.
- Receipts are structured, bounded and redacted; they never serialize environment variables, command output, tokens or model file inventories.
- Synthetic CI must not install `ffmpeg`, run `brew`/`apt`/`winget`, execute a real `pip` install through bootstrap, or download model weights.
- Existing QC safety behavior and output schema must not change.
- Development happens in an isolated worktree on a feature branch, never directly on `main`.

---

### Task 1: Create dependency-free bootstrap models and read-only checks

**Files:**
- Create: `readback_bootstrap/__init__.py`
- Create: `readback_bootstrap/models.py`
- Create: `readback_bootstrap/checks.py`
- Create: `tests/test_bootstrap_checks.py`

**Interfaces:**
- Produces immutable `PlatformInfo`, `ComponentStatus`, `Receipt`, `Step`, `StepStatus` and `OverallStatus` types for all later tasks.
- `discover_environment(root: Path, *, system: str | None = None, machine: str | None = None, python_version: tuple[int, int] | None = None, which: Callable[[str], str | None] = shutil.which) -> Receipt` reads state only.
- `Receipt.to_dict()` is JSON serializable and never includes process output or `os.environ`.

- [ ] **Step 1: Write failing contracts for a ready and a missing environment**

```python
from pathlib import Path

from readback_bootstrap.checks import discover_environment
from readback_bootstrap.models import OverallStatus


def test_check_reports_ready_python_and_never_executes_processes(tmp_path: Path) -> None:
    called = []
    receipt = discover_environment(
        tmp_path,
        system="Darwin",
        machine="arm64",
        python_version=(3, 11),
        which=lambda name: {"uv": "/bin/uv", "ffmpeg": "/bin/ffmpeg", "brew": "/opt/homebrew/bin/brew"}.get(name),
        run=lambda argv: called.append(argv),
    )
    assert receipt.command == "check"
    assert receipt.status is OverallStatus.READY
    assert called == []


def test_check_reports_action_required_for_old_python_and_missing_tools(tmp_path: Path) -> None:
    receipt = discover_environment(
        tmp_path,
        system="Windows",
        machine="AMD64",
        python_version=(3, 10),
        which=lambda _name: None,
    )
    assert receipt.status is OverallStatus.ACTION_REQUIRED
    assert {item.name for item in receipt.components if item.status.value != "READY"} >= {"python", "pip", "uv", "ffmpeg"}
```

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run pytest tests/test_bootstrap_checks.py -q
```

Expected: collection failure because `readback_bootstrap` does not exist.

- [ ] **Step 3: Implement data-only models and discovery**

Create `readback_bootstrap/models.py` with string `Enum` members:

```python
class OverallStatus(str, Enum):
    READY = "READY"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    FAILED = "FAILED"
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"

class StepStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    SKIPPED = "SKIPPED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
```

Use frozen dataclasses for `PlatformInfo`, `ComponentStatus` and `Step`. `Receipt` owns only JSON-safe scalar/list/dict fields and exposes `to_dict()` through `dataclasses.asdict` plus Enum-value normalization.

Implement `discover_environment` without calling its injected `run` function. Check:

- `root / "pyproject.toml"` and `root / "uv.lock"` exist;
- the supplied Python major/minor is at least 3.11;
- `sys.executable -m pip --version` is represented as `UNKNOWN` during `check` unless an injected `pip_available` probe says otherwise;
- `uv`, `ffmpeg` and OS package-manager executables use injected `which` only;
- platform metadata is explicit and contains no username/home path.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_bootstrap_checks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the isolated component**

```bash
git add readback_bootstrap/__init__.py readback_bootstrap/models.py readback_bootstrap/checks.py tests/test_bootstrap_checks.py
git commit -m "feat: add safe bootstrap environment checks"
```

### Task 2: Define deterministic platform-specific install plans

**Files:**
- Create: `readback_bootstrap/platforms.py`
- Create: `readback_bootstrap/plan.py`
- Create: `tests/test_bootstrap_plan.py`
- Modify: `readback_bootstrap/models.py`

**Interfaces:**
- `build_install_plan(receipt: Receipt, *, model: str | None, skip_model: bool, is_root: bool) -> tuple[Step, ...]` returns commands but does not execute them.
- `platform_ffmpeg_step(platform: PlatformInfo, *, which: Callable[[str], str | None], is_root: bool) -> Step` returns either executable `argv` or `manual_action`.
- Every `Step` declares `requires_network`, `requires_elevation`, `argv`, `manual_action`, and `id`.

- [ ] **Step 1: Write failing platform-plan tests**

```python
import pytest

from readback_bootstrap.models import PlatformInfo, StepStatus
from readback_bootstrap.platforms import platform_ffmpeg_step

@pytest.mark.parametrize(
    ("platform", "which", "is_root", "argv"),
    [
        (PlatformInfo("Darwin", "", "arm64"), lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None, False, ("brew", "install", "ffmpeg")),
        (PlatformInfo("Windows", "", "AMD64"), lambda name: "C:/Windows/winget.exe" if name == "winget" else None, False, ("winget", "install", "--id", "Gyan.FFmpeg", "-e", "--accept-source-agreements", "--accept-package-agreements")),
        (PlatformInfo("Linux", "debian", "x86_64"), lambda _name: "/usr/bin/apt-get", True, ("apt-get", "install", "-y", "ffmpeg")),
    ],
)
def test_platform_plan_uses_expected_non_shell_command(platform, which, is_root, argv) -> None:
    step = platform_ffmpeg_step(platform, which=which, is_root=is_root)
    assert step.argv == argv
    assert step.requires_elevation is False


def test_non_root_debian_requires_manual_sudo_step() -> None:
    step = platform_ffmpeg_step(PlatformInfo("Linux", "debian", "x86_64"), which=lambda _name: "/usr/bin/apt-get", is_root=False)
    assert step.status is StepStatus.MANUAL_ACTION_REQUIRED
    assert step.argv is None
    assert step.manual_action == "sudo apt-get update && sudo apt-get install -y ffmpeg"
```

- [ ] **Step 2: Run focused test to verify RED**

Run:

```bash
uv run pytest tests/test_bootstrap_plan.py -q
```

Expected: collection failure because `platforms.py` and `plan.py` do not exist.

- [ ] **Step 3: Implement platform detection and immutable planning**

Implement platform mapping exactly as the specification states. Do not use `shell=True`. For macOS without Homebrew, Windows without `winget`, non-root Debian/Ubuntu, unknown Linux and old Python, create an explicit `MANUAL_ACTION_REQUIRED` or `UNSUPPORTED_PLATFORM` step with one bounded human instruction.

Add a `pip install --user uv` `Step` only when `uv` is missing and `pip` has been confirmed available. Add `uv sync --dev --locked` only after `uv` is ready/planned. Add `prepare_model` only when `model in {"tiny", "base", "small"}` and `skip_model` is false.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_bootstrap_plan.py tests/test_bootstrap_checks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the plan layer**

```bash
git add readback_bootstrap/models.py readback_bootstrap/platforms.py readback_bootstrap/plan.py tests/test_bootstrap_plan.py
git commit -m "feat: plan portable bootstrap prerequisites"
```

### Task 3: Apply approved steps and produce redacted receipts

**Files:**
- Create: `readback_bootstrap/apply.py`
- Create: `readback_bootstrap/receipt.py`
- Create: `tests/test_bootstrap_apply.py`
- Modify: `readback_bootstrap/models.py`

**Interfaces:**
- `apply_plan(steps: tuple[Step, ...], *, assume_yes: bool, confirm: Callable[[Step], bool], run: Callable[[tuple[str, ...]], CompletedProcess[str]]) -> tuple[Step, ...]` executes only approved `argv` values.
- `write_receipt(receipt: Receipt, path: Path | None) -> str` returns compact JSON and atomically writes it only when `path` is supplied.
- A non-zero external result changes the current step to `FAILED`, leaves all later steps `NOT_RUN`, and returns without raising raw subprocess output.

- [ ] **Step 1: Write failing execution and receipt tests**

```python
from pathlib import Path
from subprocess import CompletedProcess

from readback_bootstrap.apply import apply_plan
from readback_bootstrap.models import Step, StepStatus
from readback_bootstrap.receipt import write_receipt


def test_yes_skips_elevated_step_without_running_it() -> None:
    executed = []
    steps = (Step("apt_ffmpeg", ("apt-get", "install", "-y", "ffmpeg"), True, True),)
    result = apply_plan(steps, assume_yes=True, confirm=lambda _step: True, run=lambda argv: executed.append(argv) or CompletedProcess(argv, 0, "", ""))
    assert result[0].status is StepStatus.MANUAL_ACTION_REQUIRED
    assert executed == []


def test_failed_command_stops_later_steps_and_redacts_output(tmp_path: Path) -> None:
    steps = (
        Step("install_uv", ("python", "-m", "pip", "install", "--user", "uv"), True, False),
        Step("sync", ("uv", "sync", "--dev", "--locked"), True, False),
    )
    result = apply_plan(steps, assume_yes=True, confirm=lambda _step: True, run=lambda argv: CompletedProcess(argv, 1, "TOKEN=never-show", "failure"))
    assert result[0].status is StepStatus.FAILED
    assert result[1].status is StepStatus.NOT_RUN
    text = write_receipt(receipt_from_steps(result), tmp_path / "receipt.json")
    assert "TOKEN=never-show" not in text
    assert (tmp_path / "receipt.json").is_file()
```

`receipt_from_steps` must be a test helper that constructs a minimal `Receipt`; it must not be production API.

- [ ] **Step 2: Run focused test to verify RED**

Run:

```bash
uv run pytest tests/test_bootstrap_apply.py -q
```

Expected: collection failure because `apply.py` and `receipt.py` do not exist.

- [ ] **Step 3: Implement application rules**

For interactive mode, call `confirm(step)` once immediately before each non-elevated executable step. For `--yes`, execute an executable step unless `requires_elevation` is true. Never execute `manual_action`; it is human-readable receipt data only.

Run subprocesses with `capture_output=True`, `text=True`, `shell=False`, `check=False`. Store only return code and a bounded generic safe error label in a receipt; do not store stdout/stderr. Use temporary sibling file plus `Path.replace()` for an explicit receipt file.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_bootstrap_apply.py tests/test_bootstrap_plan.py tests/test_bootstrap_checks.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit application behavior**

```bash
git add readback_bootstrap/apply.py readback_bootstrap/receipt.py readback_bootstrap/models.py tests/test_bootstrap_apply.py
git commit -m "feat: apply bootstrap plans with safe receipts"
```

### Task 4: Add the standalone CLI and explicit model-preparation helper

**Files:**
- Create: `readback_bootstrap/cli.py`
- Create: `scripts/bootstrap.py`
- Create: `scripts/prepare_model.py`
- Create: `tests/test_bootstrap_cli.py`

**Interfaces:**
- `main(argv: Sequence[str] | None = None) -> int` in `readback_bootstrap.cli` maps command outcomes to `0`, `1`, `2`, `64`.
- `scripts/bootstrap.py` imports only `readback_bootstrap.cli.main` and exits with it.
- `scripts/prepare_model.py` accepts `--model {tiny,base,small}`, `--cache-dir PATH`, `--json`; it imports `faster_whisper.WhisperModel` only after parser validation.

- [ ] **Step 1: Write failing CLI contracts**

```python
import json

from readback_bootstrap.cli import main


def test_check_json_is_single_receipt_and_has_no_apply(monkeypatch, capsys, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("readback_bootstrap.cli.discover_environment", lambda *args, **kwargs: ready_receipt())
    assert main(["check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "check"
    assert payload["smoke_test"]["status"] == "NOT_RUN"


def test_invalid_model_is_usage_error() -> None:
    assert main(["install", "--model", "large-v3", "--yes"]) == 64
```

For `prepare_model.py`, test parser behavior by loading the script with `importlib.util`; do not import or instantiate `WhisperModel` in CI.

- [ ] **Step 2: Run focused test to verify RED**

Run:

```bash
uv run pytest tests/test_bootstrap_cli.py -q
```

Expected: collection failure because the CLI modules do not exist.

- [ ] **Step 3: Implement command parsing and model helper**

CLI arguments:

```text
bootstrap.py check [--json] [--receipt PATH]
bootstrap.py install [--yes] [--model tiny|base|small] [--skip-model] [--model-cache PATH] [--json] [--receipt PATH]
```

Reject `--model` combined with `--skip-model`. `check` ignores no model flag because it must remain non-mutating. `install` calls discovery, planning, application and smoke testing in that order; it calls the helper only after sync is `COMPLETED`.

`prepare_model.py` constructs `WhisperModel(model_name, download_root=str(cache_dir))`, exits non-zero on failure, and emits a narrow JSON object. Ensure cache directory ownership mode is `0700` only on POSIX. Do not print exception stack traces by default.

- [ ] **Step 4: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_bootstrap_cli.py tests/test_bootstrap_apply.py tests/test_bootstrap_plan.py tests/test_bootstrap_checks.py -q
uv run python scripts/bootstrap.py check --json
```

Expected: all tests pass; command returns a parseable receipt without installing anything.

- [ ] **Step 5: Commit the executable bootstrap surface**

```bash
git add readback_bootstrap/ scripts/bootstrap.py scripts/prepare_model.py tests/test_bootstrap_cli.py
git commit -m "feat: add standalone portable bootstrap CLI"
```

### Task 5: Publish installation guidance, skill integration, CI matrix and release documentation

**Files:**
- Create: `INSTALL.md`
- Create: `docs/releases/v0.5.0.md`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `CHANGELOG.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Modify: `tests/test_governance_contract.py`

**Interfaces:**
- Every documented path begins with `check`; only an explicitly requested command uses `install` or `--model`.
- GitHub Actions runs `uv sync --dev --locked` and tests on macOS, Ubuntu and Windows under Python 3.11.

- [ ] **Step 1: Write failing documentation and workflow tests**

Add contracts to `tests/test_governance_contract.py`:

```python
def test_portable_bootstrap_docs_and_entrypoint_are_present() -> None:
    for path in ("INSTALL.md", "scripts/bootstrap.py", "scripts/prepare_model.py", "docs/releases/v0.5.0.md"):
        assert (ROOT / path).is_file()
    install = (ROOT / "INSTALL.md").read_text(encoding="utf-8")
    assert "bootstrap.py check" in install
    assert "--yes" in install
    assert "sudo" in install.lower()
```

Add a YAML-as-text contract checking `macos-latest`, `ubuntu-latest`, `windows-latest`, `uv sync --dev --locked`, and `uv run pytest -q` appear in the workflow.

- [ ] **Step 2: Run the governance test to verify RED**

Run:

```bash
uv run pytest tests/test_governance_contract.py -q
```

Expected: FAIL because the v0.5.0 docs and entrypoints do not exist yet.

- [ ] **Step 3: Implement documentation and CI changes**

`INSTALL.md` must cover:

- prerequisite Python 3.11+ and clone/ZIP-source acquisition;
- non-mutating `check` as the first command;
- the interactive and agent `--yes --json` modes;
- platform-specific manual escalation examples;
- explicit model permission;
- receipt interpretation and non-goals.

Change README quick start to point to `INSTALL.md` and use bootstrap before `uv sync`. Update `SKILL.md` to direct Hermes to `check --json`, inspect the receipt, ask separately for install/model permission and then use the same CLI. Add platform cache/receipt ignores to `.gitignore` without ignoring source/docs. Add a v0.5.0 changelog entry and release note that state no real-media accuracy, package-manager availability or model quality was established.

Make CI a `strategy.matrix.os` job with `macos-latest`, `ubuntu-latest`, `windows-latest`; do not add package-manager or model-download steps.

- [ ] **Step 4: Run all repository checks to verify GREEN**

Run:

```bash
uv lock --check --offline
uv run pytest -q
uv run python scripts/bootstrap.py check --json
python3 - <<'PY'
import json, subprocess
payload = json.loads(subprocess.check_output(["uv", "run", "python", "scripts/bootstrap.py", "check", "--json"], text=True))
assert payload["command"] == "check"
assert payload["smoke_test"]["status"] == "NOT_RUN"
print("bootstrap_receipt_ok")
PY
git diff --check
```

Expected: every test passes, check receipt is parseable, lock is current and diff has no whitespace errors.

- [ ] **Step 5: Commit the public release surface**

```bash
git add INSTALL.md README.md SKILL.md CHANGELOG.md .gitignore .github/workflows/ci.yml docs/releases/v0.5.0.md tests/test_governance_contract.py
git commit -m "docs: publish portable bootstrap guide"
```

### Task 6: Independent review, fresh-clone validation and release

**Files:**
- Review all changed files; no additional product code without a new task.

**Interfaces:**
- Consumes the completed feature branch.
- Produces a reviewed branch, tested fresh clone and v0.5.0 release evidence.

- [ ] **Step 1: Run an independent specification and code-quality review**

Have a read-only reviewer check:

- bootstrap imports only stdlib before dependency sync;
- no path reaches `shell=True`, privilege escalation or automatic model download;
- check mode has no mutation path;
- receipts do not serialize subprocess output, environment or client paths;
- CI matrix is synthetic only;
- documentation precisely matches commands and limitations.

Expected: APPROVE or a bounded correction list.

- [ ] **Step 2: Correct any accepted findings with focused tests**

For each finding, add a regression test first, demonstrate RED, make the minimal fix, demonstrate GREEN and rerun the affected suite.

- [ ] **Step 3: Validate a clean clone without customer inputs**

Run:

```bash
VERIFY_ROOT="$HOME/.hermes/cache/scratch/readback-sentinel-bootstrap-verify"
rm -rf "$VERIFY_ROOT"
git clone --no-local --branch feat/portable-bootstrap . "$VERIFY_ROOT"
cd "$VERIFY_ROOT"
uv sync --dev --locked
uv run pytest -q
uv run python scripts/bootstrap.py check --json
```

Expected: dependencies resolve from the committed lock, all synthetic tests pass and check produces only a safe receipt.

- [ ] **Step 4: Publish and verify remote state**

After review approval, merge the feature branch into `main`, create annotated tag `v0.5.0`, push branch/tag, create a GitHub release from `docs/releases/v0.5.0.md`, and verify by reading back:

- public repository default branch points at the release commit;
- the tag resolves to that commit;
- the release page is published and not a draft;
- all three CI matrix jobs succeeded;
- public README and INSTALL guide render from `main`.

- [ ] **Step 5: Record release completion**

```bash
git status -sb
git log -1 --decorate --oneline
git tag --points-at HEAD
```

Expected: clean `main`, visible `v0.5.0` tag and no uncommitted artifacts.
