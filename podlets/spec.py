from __future__ import annotations

import glob
import os
import shlex
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Sequence

from .common import DIRECTIVE_RE, SlError, command_dirs


@dataclass(frozen=True)
class InputPlan:
    raw: str
    stage_source: Path
    remote_value: str
    is_glob: bool


class CommandSpec:
    def __init__(self, path: Path, text: str, directives: Dict[str, List[str]]) -> None:
        self.path = path
        self.text = text
        self.directives = directives
        self.name = self._one("name") or path.stem
        self.description = self._one("description") or ""
        self.setup_version = self._one("setup-version") or "1"
        memcheck = self._one("memcheck")
        self.memcheck = memcheck is not None
        self.memcheck_default = memcheck or None
        self.inputs = self._indices("input")
        self.outputs = self._indices("output")
        overlap = set(self.inputs) & set(self.outputs)
        if overlap:
            raise SlError(f"command {self.name}: arguments cannot be both input and output: {sorted(overlap)}")
        if "sl_run" not in text:
            raise SlError(f"command {self.name}: command file must define sl_run()")

    def _one(self, key: str) -> str | None:
        values = self.directives.get(key, [])
        if not values:
            return None
        if len(values) > 1:
            raise SlError(f"command {self.path}: duplicate sl:{key} directive")
        return values[0]

    def _indices(self, key: str) -> List[int]:
        result: List[int] = []
        for value in self.directives.get(key, []):
            try:
                n = int(value)
            except ValueError as exc:
                raise SlError(f"command {self.path}: sl:{key} expects a positive argument index") from exc
            if n < 1:
                raise SlError(f"command {self.path}: sl:{key} expects a positive argument index")
            if n in result:
                raise SlError(f"command {self.path}: duplicate sl:{key} {n}")
            result.append(n)
        return sorted(result)


def parse_command(path: Path) -> CommandSpec:
    text = path.read_text(encoding="utf-8")
    directives: Dict[str, List[str]] = {}
    for line in text.splitlines():
        match = DIRECTIVE_RE.match(line)
        if match:
            directives.setdefault(match.group(1).lower(), []).append((match.group(2) or "").strip())
    return CommandSpec(path, text, directives)


def find_command(name: str, cfg: dict | None = None) -> CommandSpec:
    candidates = [name] if name.endswith(".cmd") else [name, name + ".cmd"]
    for directory in command_dirs(cfg):
        for candidate in candidates:
            path = directory / candidate
            if path.is_file():
                return parse_command(path)
    raise SlError(f"command not found: {name}; searched: {', '.join(str(p) for p in command_dirs(cfg))}")


def validate_output_arg(value: str) -> str:
    raw = value.rstrip("/")
    if not raw:
        raise SlError("output argument cannot be empty")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SlError(f"output arguments must be safe relative paths: {value}")
    return str(path)


def shell_array(values: Iterable[str]) -> str:
    return " ".join(shlex.quote(v) for v in values)


def plan_input(raw: str, remote_parent: str) -> InputPlan:
    """Plan one local input for staging while preserving glob intent remotely.

    A plain file/directory is staged directly. For a glob, only the stable
    non-glob directory prefix is staged; the remaining pattern is appended to
    its remote equivalent and is deliberately *not* expanded on the controller.
    """
    expanded = os.path.expandvars(os.path.expanduser(raw))
    if not glob.has_magic(expanded):
        source = Path(expanded).resolve()
        if not (source.exists() or source.is_symlink()):
            raise SlError(f"input not found: {raw}")
        return InputPlan(raw=raw, stage_source=source,
                         remote_value=f"{remote_parent}/{source.name}", is_glob=False)

    parts = Path(expanded).parts
    split = next((idx for idx, part in enumerate(parts) if glob.has_magic(part)), None)
    if split is None:
        raise SlError(f"could not resolve input glob: {raw}")
    prefix = parts[:split]
    source = Path(*prefix).resolve() if prefix else Path.cwd().resolve()
    if source == Path(source.anchor):
        raise SlError(f"input glob staging root is filesystem root; use a narrower path: {raw}")
    if not source.is_dir():
        raise SlError(f"input glob staging root not found: {source} (from {raw})")

    suffix = parts[split:]
    remote_base = PurePosixPath(remote_parent) / source.name
    remote_value = str(PurePosixPath(remote_base, *suffix))
    return InputPlan(raw=raw, stage_source=source, remote_value=remote_value, is_glob=True)


def build_arg_values(spec: CommandSpec, operands: Sequence[str], remote_root: str, job_id: str) -> Dict[int, str]:
    required = max(spec.inputs + spec.outputs + [0])
    if len(operands) < required:
        raise SlError(f"command {spec.name} requires at least {required} positional arguments")
    job_dir = f"{remote_root}/jobs/{job_id}"
    values: Dict[int, str] = {}
    for idx, raw in enumerate(operands, 1):
        if idx in spec.inputs:
            values[idx] = plan_input(raw, f"{job_dir}/input/arg{idx}").remote_value
        elif idx in spec.outputs:
            values[idx] = f"{job_dir}/output/{validate_output_arg(raw)}"
        else:
            values[idx] = raw
    return values


def manifest_for_job(*, job_id: str, spec: CommandSpec, operands: Sequence[str], extra_args: Sequence[str],
                     output_dir: Path, remote_root: str, arg_values: Dict[int, str]) -> dict:
    job_dir = f"{remote_root}/jobs/{job_id}"
    inputs = []
    for idx in spec.inputs:
        plan = plan_input(operands[idx - 1], f"{job_dir}/input/arg{idx}")
        inputs.append({
            "arg": idx,
            "local": plan.raw,
            "staged_from": str(plan.stage_source),
            "glob": plan.is_glob,
            "remote": arg_values[idx],
        })
    return {
        "schema": 1,
        "job_id": job_id,
        "command": spec.name,
        "command_file": spec.path.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operands": list(operands),
        "extra_args": list(extra_args),
        "inputs": inputs,
        "outputs": [
            {"arg": idx, "requested": validate_output_arg(operands[idx - 1]), "remote": arg_values[idx]}
            for idx in spec.outputs
        ],
        "local_output_dir": str(output_dir.expanduser().resolve()),
        "remote_job_dir": job_dir,
    }


def build_run_script(*, job_id: str, spec: CommandSpec, arg_values: Dict[int, str], extra_args: Sequence[str],
                     remote_root: str, runtime_repo: str, runtime_ref: str, memory_mib: int | None = None,
                     verbosity_mode: str = "run") -> str:
    job_dir = f"{remote_root}/jobs/{job_id}"
    cache_dir = f"{remote_root}/cache"
    runtime_dir = f"{remote_root}/runtime/pod-runtime"
    command_cache = f"{cache_dir}/commands/{spec.name}"
    arg_lines = [f"export SL_ARG_{idx}={shlex.quote(value)}" for idx, value in sorted(arg_values.items())]
    memory_line = f"export SL_MEMORY_REQUIRED_MIB={memory_mib}" if memory_mib is not None else "unset SL_MEMORY_REQUIRED_MIB"
    telemetry_line = f"export SL_GPU_TELEMETRY_ENABLED={'1' if spec.memcheck else '0'}"
    extras = shell_array(extra_args)
    return f'''#!/usr/bin/env bash
set -uo pipefail

export SL_JOB_ID={shlex.quote(job_id)}
export SL_JOB_DIR={shlex.quote(job_dir)}
export SL_INPUT_DIR="$SL_JOB_DIR/input"
export SL_OUTPUT_DIR="$SL_JOB_DIR/output"
export SL_WORK_DIR="$SL_JOB_DIR/work"
export SL_CACHE_DIR={shlex.quote(cache_dir)}
export SL_REMOTE_ROOT={shlex.quote(remote_root)}
export SL_RUNTIME_DIR={shlex.quote(runtime_dir)}
export SL_COMMAND_NAME={shlex.quote(spec.name)}
export SL_COMMAND_CACHE={shlex.quote(command_cache)}
export SL_COMMAND_FILE="$SL_JOB_DIR/command.cmd"
export SL_STATUS_FILE="$SL_JOB_DIR/status.json"
export SL_LOG_FILE="$SL_JOB_DIR/job.log"
export SL_DISPLAY_LOG="$SL_JOB_DIR/display.log"
export SL_GPU_TELEMETRY_FILE="$SL_JOB_DIR/gpu-telemetry.json"
export SL_GPU_SAMPLES_FILE="$SL_JOB_DIR/gpu-memory.samples"
export SL_VERBOSITY={shlex.quote(verbosity_mode)}
{os.linesep.join(arg_lines)}
SL_EXTRA_ARGS=({extras})
{memory_line}
{telemetry_line}
export POD_RUNTIME_DIR="$SL_RUNTIME_DIR"
export PYTHONUNBUFFERED=1

_sl_now() {{ date -Is; }}
_sl_epoch_ms() {{ date +%s%3N; }}
_sl_emit() {{
  local level="$1"; shift
  local line="$(_sl_now) [sl] $*"
  printf '%s\\n' "$line"
  if [[ "$SL_VERBOSITY" == "full" || "$SL_VERBOSITY" == "debug" || "$level" == "major" ]]; then
    printf '%s\\n' "$line" >> "$SL_DISPLAY_LOG"
  fi
}}
_sl_should_show_phase() {{
  local phase="$1"
  [[ "$SL_VERBOSITY" == "full" || "$SL_VERBOSITY" == "debug" || ( "$SL_VERBOSITY" == "run" && "$phase" == "RUN" ) ]]
}}
_sl_phase() {{
  local phase="$1"; shift
  if _sl_should_show_phase "$phase"; then
    "$@" > >(tee -a "$SL_DISPLAY_LOG") 2> >(tee -a "$SL_DISPLAY_LOG" >&2)
  else
    "$@"
  fi
}}
_sl_status() {{
  local state="$1" code="${{2:-}}" started completed
  started="$(cat "$SL_JOB_DIR/started_at" 2>/dev/null || true)"
  completed=""
  if [[ "$state" == "SUCCEEDED" || "$state" == "FAILED" || "$state" == "COMPLETE" ]]; then completed="$(_sl_now)"; fi
  SL_STATE="$state" SL_CODE="$code" SL_STARTED="$started" SL_COMPLETED="$completed" \\
    python3 - "$SL_STATUS_FILE" "$SL_GPU_TELEMETRY_FILE" <<'PY_STATUS'
import json, os, pathlib, sys
p = pathlib.Path(sys.argv[1])
telemetry_path = pathlib.Path(sys.argv[2])
required = os.environ.get("SL_MEMORY_REQUIRED_MIB")
free = os.environ.get("SL_MEMORY_FREE_MIB")
telemetry = None
if telemetry_path.is_file():
    try:
        value = json.loads(telemetry_path.read_text(encoding="utf-8"))
        telemetry = value if isinstance(value, dict) else None
    except Exception:
        telemetry = None
data = {{
  "state": os.environ["SL_STATE"],
  "exit_code": int(os.environ["SL_CODE"]) if os.environ.get("SL_CODE") not in (None, "") else None,
  "started_at": os.environ.get("SL_STARTED") or None,
  "completed_at": os.environ.get("SL_COMPLETED") or None,
  "memory_required_mib": int(required) if required else None,
  "memory_free_mib": int(free) if free else None,
  "gpu_telemetry": telemetry,
}}
tmp = p.with_suffix(".tmp")
tmp.write_text(json.dumps(data, indent=2) + "\\n")
tmp.replace(p)
PY_STATUS
}}

_sl_wait_for_memory() {{
  local required="${{SL_MEMORY_REQUIRED_MIB:-}}" total free now last_log=0
  [[ -n "$required" ]] || return 0
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    _sl_emit major "ERROR: --mem requested but nvidia-smi is unavailable"; _sl_status FAILED 127; return 127
  fi
  total="$(nvidia-smi -i 0 --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d '[:space:]')"
  if [[ ! "$total" =~ ^[0-9]+$ ]]; then _sl_emit major "ERROR: could not query GPU memory total"; _sl_status FAILED 2; return 2; fi
  if (( required > total )); then _sl_emit major "ERROR: memory requirement ${{required}} MiB exceeds GPU total ${{total}} MiB"; _sl_status FAILED 2; return 2; fi
  _sl_emit major "memory gate enabled: require ${{required}} MiB free GPU VRAM (GPU total ${{total}} MiB)"
  while :; do
    free="$(nvidia-smi -i 0 --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d '[:space:]')"
    if [[ ! "$free" =~ ^[0-9]+$ ]]; then _sl_emit major "ERROR: could not query free GPU memory"; _sl_status FAILED 2; return 2; fi
    export SL_MEMORY_FREE_MIB="$free"
    _sl_status WAITING_FOR_MEMORY
    if (( free >= required )); then _sl_emit major "memory gate satisfied: ${{free}} MiB free >= ${{required}} MiB required"; return 0; fi
    now="$(date +%s)"
    if (( last_log == 0 || now - last_log >= 30 )); then
      _sl_emit major "waiting for GPU memory: ${{free}} MiB free, ${{required}} MiB required"; last_log="$now"
    fi
    sleep 5
  done
}}

_sl_gpu_monitor_start() {{
  SL_GPU_MONITOR_PID=""
  SL_GPU_TOTAL_MIB=""
  SL_GPU_BASELINE_USED_MIB=""
  [[ "$SL_GPU_TELEMETRY_ENABLED" == "1" ]] || return 0
  command -v nvidia-smi >/dev/null 2>&1 || return 0

  SL_GPU_TOTAL_MIB="$(nvidia-smi -i 0 --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d '[:space:]')"
  SL_GPU_BASELINE_USED_MIB="$(nvidia-smi -i 0 --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d '[:space:]')"
  if [[ ! "$SL_GPU_TOTAL_MIB" =~ ^[0-9]+$ || ! "$SL_GPU_BASELINE_USED_MIB" =~ ^[0-9]+$ ]]; then
    SL_GPU_TOTAL_MIB=""; SL_GPU_BASELINE_USED_MIB=""; return 0
  fi

  rm -f "$SL_GPU_TELEMETRY_FILE" "$SL_GPU_SAMPLES_FILE"
  : > "$SL_GPU_SAMPLES_FILE"
  nvidia-smi -i 0 --query-gpu=memory.used --format=csv,noheader,nounits --loop-ms=500 \
    > "$SL_GPU_SAMPLES_FILE" 2>/dev/null &
  SL_GPU_MONITOR_PID=$!
}}

_sl_gpu_monitor_stop_and_report() {{
  local run_start_ms="$1" run_end_ms="$2" required="${{SL_MEMORY_REQUIRED_MIB:-}}" final_used=""
  if [[ -n "${{SL_GPU_MONITOR_PID:-}}" ]]; then
    kill "$SL_GPU_MONITOR_PID" 2>/dev/null || true
    wait "$SL_GPU_MONITOR_PID" 2>/dev/null || true
  fi
  [[ -n "${{SL_GPU_TOTAL_MIB:-}}" && -n "${{SL_GPU_BASELINE_USED_MIB:-}}" ]] || return 0

  final_used="$(nvidia-smi -i 0 --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1 | tr -d '[:space:]')"
  if [[ "$final_used" =~ ^[0-9]+$ ]]; then printf '%s\\n' "$final_used" >> "$SL_GPU_SAMPLES_FILE"; fi

  python3 - "$SL_GPU_TOTAL_MIB" "$SL_GPU_BASELINE_USED_MIB" "$SL_GPU_SAMPLES_FILE" \
    "$SL_GPU_TELEMETRY_FILE" "$run_start_ms" "$run_end_ms" "$required" <<'PY_GPU'
import json
import math
import pathlib
import sys

total = int(sys.argv[1])
baseline = int(sys.argv[2])
samples_path = pathlib.Path(sys.argv[3])
out_path = pathlib.Path(sys.argv[4])
start_ms = int(sys.argv[5])
end_ms = int(sys.argv[6])
required = int(sys.argv[7]) if sys.argv[7] else None

samples = []
if samples_path.is_file():
    for raw in samples_path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if raw.isdigit():
            samples.append(int(raw))
peak = max([baseline, *samples])
delta = max(0, peak - baseline)
run_seconds = max(0.0, (end_ms - start_ms) / 1000.0)
headroom = max(1024, math.ceil(delta * 0.10))
suggested_mib = max(1024, math.ceil((delta + headroom) / 1024) * 1024)
suggested = "%dG" % (suggested_mib // 1024)

payload = {{
    "gpu_index": 0,
    "sample_interval_ms": 500,
    "sample_count": len(samples),
    "total_mib": total,
    "baseline_used_mib": baseline,
    "peak_used_mib": peak,
    "peak_above_baseline_mib": delta,
    "run_seconds": round(run_seconds, 3),
    "suggested_memcheck_mib": suggested_mib,
    "suggested_memcheck": suggested,
    "requested_memcheck_mib": required,
}}
out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY_GPU

  while IFS= read -r line; do
    _sl_emit major "$line"
  done < <(python3 - "$SL_GPU_TELEMETRY_FILE" <<'PY_REPORT'
import json, sys
p=json.load(open(sys.argv[1], encoding='utf-8'))
def gib(v): return "%.1f GiB" % (v / 1024)
print("============================================================")
print("GPU RUN TELEMETRY (GPU 0; 500 ms sampling)")
print("  BASELINE GPU VRAM:       %s" % gib(p['baseline_used_mib']))
print("  MAX GPU VRAM OBSERVED:   %s / %s" % (gib(p['peak_used_mib']), gib(p['total_mib'])))
print("  PEAK ABOVE BASELINE:     %s" % gib(p['peak_above_baseline_mib']))
print("  RUN TIME:                %.1fs" % p['run_seconds'])
print("  SUGGESTED --mem:         %s (10%% headroom; minimum +1 GiB)" % p['suggested_memcheck'])
if p.get('requested_memcheck_mib') is not None:
    print("  CONFIGURED --mem:        %s" % gib(p['requested_memcheck_mib']))
print("============================================================")
PY_REPORT
  )
}}

_sl_status PREPARING
_sl_emit major "job $SL_JOB_ID preparing command $SL_COMMAND_NAME"

for env_file in /etc/rp_environment /root/.secrets/env.current; do
  if [[ -f "$env_file" ]]; then set +u; source "$env_file"; set -u; fi
done
if [[ -n "${{SL_CONTROLLER_HF_TOKEN:-}}" ]]; then
  export HF_TOKEN="$SL_CONTROLLER_HF_TOKEN" HUGGINGFACE_HUB_TOKEN="$SL_CONTROLLER_HF_TOKEN"
  unset SL_CONTROLLER_HF_TOKEN
fi

mkdir -p "$(dirname "$SL_RUNTIME_DIR")" "$SL_CACHE_DIR" "$SL_COMMAND_CACHE" "$SL_WORK_DIR"
if [[ -d "$SL_RUNTIME_DIR/.git" ]]; then
  _sl_emit debug "refreshing pod-runtime snapshot ($SL_RUNTIME_DIR)"
else
  _sl_emit debug "shallow cloning pod-runtime"
  rm -rf "$SL_RUNTIME_DIR"
  git clone --quiet --depth 1 --no-tags {shlex.quote(runtime_repo)} "$SL_RUNTIME_DIR"
fi
if ! git -C "$SL_RUNTIME_DIR" fetch --quiet --depth 1 --no-tags origin {shlex.quote(runtime_ref)}; then
  _sl_emit major "ERROR: could not fetch pod-runtime ref {runtime_ref}"; _sl_status FAILED 127; exit 127
fi
git -C "$SL_RUNTIME_DIR" checkout --quiet --detach FETCH_HEAD
if [[ ! -f "$SL_RUNTIME_DIR/helpers.sh" ]]; then _sl_emit major "ERROR: pod-runtime/helpers.sh unavailable"; _sl_status FAILED 127; exit 127; fi
export repo_root="$SL_RUNTIME_DIR"
set +u
source "$SL_RUNTIME_DIR/helpers.sh"
set -u
_sl_emit debug "pod-runtime helper stack loaded: $SL_RUNTIME_DIR"

source "$SL_COMMAND_FILE"
if declare -F sl_prepare >/dev/null 2>&1; then
  _sl_emit debug "preparing command runtime"
  _sl_phase PREPARE sl_prepare; rc=$?
  if [[ $rc -ne 0 ]]; then _sl_emit major "ERROR: sl_prepare failed with exit $rc"; _sl_status FAILED "$rc"; exit "$rc"; fi
fi

setup_version={shlex.quote(spec.setup_version)}
setup_marker="$SL_COMMAND_CACHE/setup.version"
if declare -F sl_setup >/dev/null 2>&1; then
  current="$(cat "$setup_marker" 2>/dev/null || true)"
  if [[ "$current" != "$setup_version" ]]; then
    _sl_emit debug "cold setup for $SL_COMMAND_NAME (version $setup_version)"
    _sl_phase SETUP sl_setup; rc=$?
    if [[ $rc -ne 0 ]]; then _sl_emit major "ERROR: sl_setup failed with exit $rc"; _sl_status FAILED "$rc"; exit "$rc"; fi
    printf '%s\\n' "$setup_version" > "$setup_marker"
  else
    _sl_emit debug "warm setup cache hit for $SL_COMMAND_NAME (version $setup_version)"
  fi
fi

_sl_wait_for_memory; rc=$?; [[ $rc -eq 0 ]] || exit "$rc"
_sl_status RUNNING
_sl_emit major "running command"
_sl_gpu_monitor_start
run_start_ms="$(_sl_epoch_ms)"
_sl_phase RUN sl_run; rc=$?
run_end_ms="$(_sl_epoch_ms)"
_sl_gpu_monitor_stop_and_report "$run_start_ms" "$run_end_ms"
if [[ $rc -eq 0 ]]; then _sl_emit major "command completed successfully"; _sl_status SUCCEEDED 0
else _sl_emit major "command failed with exit $rc"; _sl_status FAILED "$rc"; fi
exit "$rc"
'''
