from __future__ import annotations

import base64
import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Sequence

from .common import ACTIVE_STATES, SlError, info, local_job_dir, read_json, remote_job_dir, ssh, validate_output_dir, vcp
from .spec import CommandSpec, plan_input


def prepare_remote_job(job_id: str, spec: CommandSpec, manifest: dict, run_script: str, cfg: dict) -> None:
    job_dir = remote_job_dir(job_id, cfg)
    encoded_command = base64.b64encode(spec.text.encode()).decode()
    encoded_manifest = base64.b64encode((json.dumps(manifest, indent=2) + "\n").encode()).decode()
    encoded_runner = base64.b64encode(run_script.encode()).decode()
    encoded_status = base64.b64encode((json.dumps({"state": "CREATED", "exit_code": None}, indent=2) + "\n").encode()).decode()
    output_parents = [str(PurePosixPath(str(x["remote"])).parent) for x in manifest.get("outputs", []) if isinstance(x, dict) and x.get("remote")]
    mkdir_outputs = "\n".join(f"mkdir -p {shlex.quote(path)}" for path in output_parents)
    ssh(f'''set -euo pipefail
job={shlex.quote(job_dir)}
mkdir -p "$job/input" "$job/output" "$job/work"
{mkdir_outputs}
printf '%s' {shlex.quote(encoded_command)} | base64 -d > "$job/command.cmd"
printf '%s' {shlex.quote(encoded_manifest)} | base64 -d > "$job/manifest.json"
printf '%s' {shlex.quote(encoded_runner)} | base64 -d > "$job/run.sh"
printf '%s' {shlex.quote(encoded_status)} | base64 -d > "$job/status.json"
: > "$job/job.log"
: > "$job/display.log"
chmod 700 "$job/run.sh"
''')


def stage_inputs(
    job_id: str, spec: CommandSpec, operands: Sequence[str], cfg: dict, *, verbosity_mode: str | None = None
) -> None:
    job_dir = remote_job_dir(job_id, cfg)
    if not spec.inputs:
        return
    transport_log = local_job_dir(job_id, cfg) / "transport.log"
    ssh(f"printf '%s\\n' '{{\"state\": \"STAGING\", \"exit_code\": null}}' > {shlex.quote(job_dir + '/status.json')}\n")
    for idx in spec.inputs:
        remote_parent = f"{job_dir}/input/arg{idx}"
        plan = plan_input(operands[idx - 1], remote_parent)
        local = str(plan.stage_source)
        ssh(f"mkdir -p {shlex.quote(remote_parent)}\n")
        if plan.is_glob:
            info(f"staging input arg{idx} root for glob {plan.raw!r}: {local}")
        else:
            info(f"staging input arg{idx}: {local}")
        vcp([local, f"r:{remote_parent}/"], cfg, verbosity_mode=verbosity_mode, log_path=transport_log)


def launch_job(job_id: str, cfg: dict) -> int:
    job_dir = remote_job_dir(job_id, cfg)
    token = os.environ.get("HF_TOKEN", "")
    token_assignment = f"SL_CONTROLLER_HF_TOKEN={shlex.quote(token)} " if token else ""
    result = ssh(f'''set -euo pipefail
job={shlex.quote(job_dir)}
date -Is > "$job/started_at"
if command -v setsid >/dev/null 2>&1; then
  {token_assignment}nohup setsid bash "$job/run.sh" >> "$job/job.log" 2>&1 < /dev/null &
else
  {token_assignment}nohup bash "$job/run.sh" >> "$job/job.log" 2>&1 < /dev/null &
fi
pid=$!
printf '%s\\n' "$pid" > "$job/pid"
printf '%s\\n' "$pid"
''', capture=True)
    try:
        return int((result.stdout or "").strip().splitlines()[-1])
    except Exception as exc:
        raise SlError(f"could not determine remote job pid from: {result.stdout!r}") from exc


def remote_status(job_id: str, cfg: dict, *, allow_missing: bool = False) -> dict | None:
    result = ssh(f"cat {shlex.quote(remote_job_dir(job_id, cfg) + '/status.json')}\n", capture=True, check=False)
    if result.returncode != 0:
        if allow_missing:
            return None
        raise SlError(f"remote job not found: {job_id}")
    try:
        value = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SlError(f"invalid remote status for {job_id}") from exc
    return value if isinstance(value, dict) else None


def local_status(job_id: str, cfg: dict) -> dict | None:
    value = read_json(local_job_dir(job_id, cfg) / "status.json", default=None)
    return value if isinstance(value, dict) else None


def sync_metadata(job_id: str, cfg: dict) -> None:
    job_dir = remote_job_dir(job_id, cfg)
    names = ["manifest.json", "status.json", "job.log", "display.log", "command.cmd", "run.sh"]
    result = ssh(f'''python3 - {shlex.quote(job_dir)} <<'PY_META'
import base64, json, pathlib, sys
root=pathlib.Path(sys.argv[1]); names={names!r}; out={{}}
for name in names:
    p=root/name
    if p.is_file(): out[name]=base64.b64encode(p.read_bytes()).decode()
print(json.dumps(out))
PY_META
''', capture=True, check=False)
    if result.returncode != 0:
        return
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return
    local = local_job_dir(job_id, cfg)
    local.mkdir(parents=True, exist_ok=True)
    for name, encoded in payload.items():
        if name in names and isinstance(encoded, str):
            try:
                (local / name).write_bytes(base64.b64decode(encoded))
            except Exception:
                pass


def follow_remote_log(job_id: str, cfg: dict, *, lines: str, follow: bool, display: bool = False) -> int:
    job_dir = remote_job_dir(job_id, cfg)
    log = job_dir + ("/display.log" if display else "/job.log")
    if not follow:
        cmd = f"cat {shlex.quote(log)}\n" if lines == "+1" else f"tail -n {shlex.quote(lines)} {shlex.quote(log)}\n"
        return ssh(cmd, check=False).returncode
    if lines == "+1":
        tail_arg = "-n +1"
    else:
        if not re.fullmatch(r"[0-9]+", lines):
            raise SlError("tail line count must be numeric")
        tail_arg = f"-n {lines}"
    return ssh(f'''set -euo pipefail
log={shlex.quote(log)}
pidfile={shlex.quote(job_dir + '/pid')}
status_file={shlex.quote(job_dir + '/status.json')}
state="$(python3 - "$status_file" <<'PY_STATE' 2>/dev/null || true
import json, sys
try: print(json.load(open(sys.argv[1])).get("state", ""))
except Exception: pass
PY_STATE
)"
if [[ "$state" != "SUCCEEDED" && "$state" != "FAILED" && "$state" != "COMPLETE" ]] && [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
  exec tail {tail_arg} --pid="$(cat "$pidfile")" -F "$log"
else
  exec tail {tail_arg} "$log"
fi
''', check=False).returncode


def load_manifest(job_id: str, cfg: dict) -> dict:
    local = read_json(local_job_dir(job_id, cfg) / "manifest.json", default=None)
    if isinstance(local, dict):
        return local
    result = ssh(f"cat {shlex.quote(remote_job_dir(job_id, cfg) + '/manifest.json')}\n", capture=True, check=False)
    if result.returncode != 0:
        raise SlError(f"manifest unavailable for job {job_id}")
    try:
        value = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise SlError(f"invalid manifest for {job_id}") from exc
    if not isinstance(value, dict):
        raise SlError(f"invalid manifest for {job_id}")
    return value


def fetch_outputs(
    job_id: str, cfg: dict, output_dir: Path | None = None, *, verbosity_mode: str | None = None
) -> list[Path]:
    manifest = load_manifest(job_id, cfg)
    status = remote_status(job_id, cfg, allow_missing=True) or local_status(job_id, cfg)
    if status and status.get("state") in ACTIVE_STATES:
        raise SlError(f"job {job_id} is still {status.get('state')}")
    outputs = manifest.get("outputs", [])
    if not isinstance(outputs, list):
        raise SlError(f"invalid outputs in manifest for {job_id}")
    dest_root = validate_output_dir(output_dir or Path(str(manifest.get("local_output_dir") or ".")))
    transport_log = local_job_dir(job_id, cfg) / "transport.log"
    fetched: list[Path] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        requested, remote = str(item.get("requested") or ""), str(item.get("remote") or "")
        if not requested or not remote:
            continue
        rel = PurePosixPath(requested)
        local_parent = dest_root.joinpath(*rel.parts[:-1]); local_parent.mkdir(parents=True, exist_ok=True)
        if ssh(f"test -e {shlex.quote(remote)} -o -L {shlex.quote(remote)}\n", check=False).returncode != 0:
            raise SlError(f"expected output missing on remote: {remote}")
        info(f"fetching output: {requested}")
        vcp([f"r:{remote}", str(local_parent) + "/"], cfg, verbosity_mode=verbosity_mode, log_path=transport_log)
        fetched.append(dest_root.joinpath(*rel.parts))
    sync_metadata(job_id, cfg)
    return fetched


def mark_complete(job_id: str, cfg: dict) -> None:
    path = remote_job_dir(job_id, cfg) + "/status.json"
    ssh(f'''python3 - {shlex.quote(path)} <<'PY_COMPLETE'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1]); d=json.loads(p.read_text()); d["state"]="COMPLETE"; p.write_text(json.dumps(d, indent=2)+"\\n")
PY_COMPLETE
''')


def clean_remote_job(job_id: str, cfg: dict) -> None:
    status = remote_status(job_id, cfg, allow_missing=True)
    if status and status.get("state") in ACTIVE_STATES:
        raise SlError(f"refusing to clean running job {job_id} ({status.get('state')})")
    job_dir = remote_job_dir(job_id, cfg)
    ssh(f"rm -rf {shlex.quote(job_dir + '/input')} {shlex.quote(job_dir + '/output')} {shlex.quote(job_dir + '/work')}\n")
    info(f"cleaned heavy workspace for {job_id}; logs and metadata retained")


def purge_job(job_id: str, cfg: dict, *, force: bool = False) -> None:
    status = remote_status(job_id, cfg, allow_missing=True)
    active = bool(status and status.get("state") in ACTIVE_STATES)
    if active and not force:
        raise SlError(f"refusing to purge running job {job_id}; use --force")
    job_dir = remote_job_dir(job_id, cfg)
    if active and force:
        ssh(f'''set +e
pidfile={shlex.quote(job_dir + '/pid')}
if [[ -f "$pidfile" ]]; then
  pid="$(cat "$pidfile")"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  sleep 1
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
fi
''', check=False)
    ssh(f"rm -rf {shlex.quote(job_dir)}\n", check=False)
    local = local_job_dir(job_id, cfg)
    if local.exists(): shutil.rmtree(local)
    info(f"purged job {job_id}")
