from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE_ROOT = "/workspace/.sl"
DEFAULT_COMMAND_DIR = REPO_ROOT / "commands"
DEFAULT_PODCRUMBS_COMMAND_DIRS = [
    REPO_ROOT.parent / "podcrumbs" / "commands",
    Path("~/git/podcrumbs/commands").expanduser(),
]
SL_CONFIG_PATH = Path(os.environ.get("SL_CONFIG", "~/.config/sl/config.json")).expanduser()
VCP_CONFIG_PATH = Path(os.environ.get("VCP_CONFIG", "~/.config/vcp/config.json")).expanduser()
DEFAULT_STATE_DIR = Path(os.environ.get("SL_STATE_DIR", "~/.local/state/sl/jobs")).expanduser()
DEFAULT_RUNTIME_REPO = "https://github.com/markwelshboy/pod-runtime.git"
DEFAULT_RUNTIME_REF = "main"
DEFAULT_VERBOSITY = "run"
VERBOSITY_LEVELS = {"none", "run", "debug", "full"}
TERMINAL_STATES = {"SUCCEEDED", "FAILED", "COMPLETE"}
ACTIVE_STATES = {"CREATED", "STAGING", "PREPARING", "WAITING_FOR_MEMORY", "RUNNING", "FETCHING"}
DIRECTIVE_RE = re.compile(r"^\s*#\s*sl:([a-zA-Z0-9_-]+)(?:\s+(.*?))?\s*$")
JOB_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$")


class SlError(RuntimeError):
    pass


def info(msg: str) -> None:
    print(f"[sl] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[sl] WARNING: {msg}", file=sys.stderr)


def die(msg: str, code: int = 1) -> int:
    print(f"[sl] ERROR: {msg}", file=sys.stderr)
    return code


def read_json(path: Path, *, default: object) -> object:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SlError(f"could not read {path}: {exc}") from exc


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def sl_config() -> dict:
    value = read_json(SL_CONFIG_PATH, default={})
    if not isinstance(value, dict):
        raise SlError(f"invalid config {SL_CONFIG_PATH}: expected JSON object")
    return value


def write_sl_config(cfg: dict) -> None:
    write_json(SL_CONFIG_PATH, cfg)


def vcp_config() -> dict:
    value = read_json(VCP_CONFIG_PATH, default={})
    if not isinstance(value, dict):
        raise SlError(f"invalid vcp config {VCP_CONFIG_PATH}: expected JSON object")
    return value


def ssh_argv() -> List[str]:
    value = vcp_config().get("ssh")
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise SlError("vcp SSH remote is not configured; run: vcp config ssh [ssh options] user@host")
    return list(value)


def remote_root(cfg: dict | None = None) -> str:
    cfg = sl_config() if cfg is None else cfg
    value = os.environ.get("SL_REMOTE_ROOT") or cfg.get("remote_root") or DEFAULT_REMOTE_ROOT
    if not isinstance(value, str) or not value.startswith("/") or value == "/":
        raise SlError("SL remote root must be an absolute non-root path")
    return value.rstrip("/")


def state_dir(cfg: dict | None = None) -> Path:
    cfg = sl_config() if cfg is None else cfg
    raw = os.environ.get("SL_STATE_DIR") or cfg.get("state_dir")
    return Path(raw).expanduser() if raw else DEFAULT_STATE_DIR


def command_dirs(cfg: dict | None = None) -> List[Path]:
    cfg = sl_config() if cfg is None else cfg
    raw = cfg.get("command_dir")
    dirs: List[Path] = []
    if isinstance(raw, str) and raw:
        dirs.append(Path(raw).expanduser())
    dirs.append(DEFAULT_COMMAND_DIR)
    dirs.extend(DEFAULT_PODCRUMBS_COMMAND_DIRS)
    seen: set[str] = set()
    result: List[Path] = []
    for path in dirs:
        key = str(path.resolve()) if path.exists() else str(path)
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def cleanup_policy(cfg: dict | None = None) -> str:
    cfg = sl_config() if cfg is None else cfg
    value = cfg.get("cleanup", "successful")
    if value not in {"never", "successful", "always"}:
        raise SlError(f"invalid cleanup policy: {value}")
    return value


def verbosity(cfg: dict | None = None, override: str | None = None) -> str:
    cfg = sl_config() if cfg is None else cfg
    value = override or os.environ.get("SL_VERBOSITY") or cfg.get("verbosity") or DEFAULT_VERBOSITY
    if value not in VERBOSITY_LEVELS:
        raise SlError(f"invalid verbosity: {value}; expected none|run|debug|full")
    return str(value)


def runtime_repo(cfg: dict | None = None) -> str:
    cfg = sl_config() if cfg is None else cfg
    return str(cfg.get("runtime_repo") or DEFAULT_RUNTIME_REPO)


def runtime_ref(cfg: dict | None = None) -> str:
    cfg = sl_config() if cfg is None else cfg
    return str(cfg.get("runtime_ref") or DEFAULT_RUNTIME_REF)


def validate_output_dir(path: Path) -> Path:
    requested = path.expanduser()
    resolved = requested.resolve()
    if not resolved.exists():
        raise SlError(f"output directory does not exist: {requested} (resolved: {resolved})")
    if not resolved.is_dir():
        raise SlError(f"output directory is not a directory: {resolved}")
    probe: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=".sl-write-test-", dir=resolved)
        os.close(fd)
        probe = Path(name)
    except OSError as exc:
        raise SlError(f"output directory is not writable: {resolved}: {exc}") from exc
    finally:
        if probe is not None:
            try:
                probe.unlink()
            except FileNotFoundError:
                pass
    return resolved


def vcp_path(cfg: dict | None = None) -> Path:
    cfg = sl_config() if cfg is None else cfg
    candidates: list[Path] = []
    override = os.environ.get("SL_VCP") or cfg.get("vcp")
    if isinstance(override, str) and override:
        candidates.append(Path(override).expanduser())
    found = shutil.which("vcp")
    if found:
        candidates.append(Path(found))
    candidates.extend([
        REPO_ROOT.parent / "pod-runtime" / "vcp",
        Path("~/git/pod-runtime/vcp").expanduser(),
    ])
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise SlError(
        "vcp launcher not found; put vcp on PATH, set SL_VCP, or run: sl config vcp /path/to/pod-runtime/vcp"
    )


def run_process(
    cmd: Sequence[str], *, check: bool = True, capture: bool = False, input_text: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd), check=check, text=True, input=input_text,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def ssh(script: str, *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return run_process(["ssh", *ssh_argv(), "bash", "-s"], check=check, capture=capture, input_text=script)


def vcp(
    args: Sequence[str], cfg: dict | None = None, *, verbosity_mode: str | None = None,
    log_path: Path | None = None,
) -> None:
    mode = verbosity(cfg, verbosity_mode)
    cmd = [str(vcp_path(cfg)), *args]
    if mode == "full":
        run_process(cmd)
        return
    result = run_process(cmd, check=False, capture=True)
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(cmd)}\n")
            if result.stdout:
                fh.write(result.stdout)
                if not result.stdout.endswith("\n"):
                    fh.write("\n")
            if result.stderr:
                fh.write(result.stderr)
                if not result.stderr.endswith("\n"):
                    fh.write("\n")
            fh.write(f"[exit {result.returncode}]\n\n")
    if result.returncode != 0:
        where = f"; see {log_path}" if log_path is not None else ""
        raise SlError(f"vcp failed with exit {result.returncode}{where}")


def job_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]


def validate_job_id(value: str) -> str:
    if not JOB_ID_RE.fullmatch(value):
        raise SlError(f"invalid job id: {value}")
    return value


def remote_job_dir(value: str, cfg: dict | None = None) -> str:
    return f"{remote_root(cfg)}/jobs/{validate_job_id(value)}"


def local_job_dir(value: str, cfg: dict | None = None) -> Path:
    return state_dir(cfg) / validate_job_id(value)
