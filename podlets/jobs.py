from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Dict

from .common import SlError, cleanup_policy, command_dirs, info, job_id, local_job_dir, read_json, remote_job_dir, remote_root, runtime_ref, runtime_repo, sl_config, ssh, ssh_argv, state_dir, validate_job_id, warn, write_json
from .memory import format_memory_mib, preflight_capacity
from .remote import clean_remote_job, fetch_outputs, follow_remote_log, launch_job, load_manifest, local_status, mark_complete, prepare_remote_job, purge_job, remote_status, stage_inputs, sync_metadata
from .spec import CommandSpec, build_arg_values, build_run_script, find_command, manifest_for_job, parse_command


def run_job(args: argparse.Namespace) -> int:
    cfg = sl_config(); ssh_argv()
    spec = find_command(args.command, cfg)
    requested_mem = args.mem
    if requested_mem is not None and not spec.memcheck:
        raise SlError(f"command {spec.name} does not declare '# sl:memcheck'; refusing --mem")
    if requested_mem is None and spec.memcheck_default:
        requested_mem = spec.memcheck_default
    from .memory import parse_memory_mib
    memory_mib = parse_memory_mib(requested_mem) if requested_mem else None
    if memory_mib is not None:
        total, free = preflight_capacity(memory_mib)
        info(f"GPU memory now: {format_memory_mib(free)} free / {format_memory_mib(total)} total")

    operands = list(args.operands)
    jid = job_id(); root = remote_root(cfg)
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else Path(".")
    values = build_arg_values(spec, operands, root, jid)
    manifest = manifest_for_job(job_id=jid, spec=spec, operands=operands, extra_args=args.extra, output_dir=output_dir, remote_root=root, arg_values=values)
    manifest["memory"] = {"memcheck": spec.memcheck, "requested": requested_mem, "required_mib": memory_mib}
    local = local_job_dir(jid, cfg); local.mkdir(parents=True, exist_ok=True)
    write_json(local / "manifest.json", manifest); write_json(local / "status.json", {"state": "CREATED", "exit_code": None})
    runner = build_run_script(job_id=jid, spec=spec, arg_values=values, extra_args=args.extra, remote_root=root,
                              runtime_repo=runtime_repo(cfg), runtime_ref=runtime_ref(cfg), memory_mib=memory_mib)
    info(f"job: {jid}"); info(f"command: {spec.name}")
    if memory_mib is not None: info(f"memory gate: require {format_memory_mib(memory_mib)} free GPU VRAM")
    prepare_remote_job(jid, spec, manifest, runner, cfg)
    try:
        stage_inputs(jid, spec, operands, cfg); pid = launch_job(jid, cfg)
    except Exception:
        sync_metadata(jid, cfg); raise
    info(f"remote pid: {pid}")
    if args.detach:
        info(f"submitted: {jid}"); info(f"follow with: sl tail {jid}"); sync_metadata(jid, cfg); return 0
    follow_remote_log(jid, cfg, lines="+1", follow=True)
    return finalize_sync_job(jid, cfg, output_dir=output_dir, no_fetch=args.no_fetch, keep_remote=args.keep_remote)


def finalize_sync_job(jid: str, cfg: dict, *, output_dir: Path | None, no_fetch: bool, keep_remote: bool) -> int:
    sync_metadata(jid, cfg)
    status = remote_status(jid, cfg, allow_missing=True) or local_status(jid, cfg) or {}
    state, exit_code = str(status.get("state") or "UNKNOWN"), status.get("exit_code")
    manifest = load_manifest(jid, cfg); has_outputs = bool(manifest.get("outputs")); fetched = False
    if state == "SUCCEEDED" and not no_fetch:
        info("job succeeded; fetching outputs"); fetch_outputs(jid, cfg, output_dir); fetched = True; mark_complete(jid, cfg); sync_metadata(jid, cfg); state = "COMPLETE"
    policy = cleanup_policy(cfg)
    cleanup_ok = state in {"SUCCEEDED", "COMPLETE"} and (fetched or not has_outputs)
    if not keep_remote and (policy == "always" or (policy == "successful" and cleanup_ok)):
        clean_remote_job(jid, cfg)
    if state in {"SUCCEEDED", "COMPLETE"}:
        info(f"job {jid}: {state}"); return 0
    info(f"job {jid}: {state} (exit {exit_code})")
    return int(exit_code) if isinstance(exit_code, int) and exit_code else 1


def jobs(cfg: dict) -> int:
    root = remote_root(cfg)
    result = ssh(f'''python3 - {shlex.quote(root + '/jobs')} <<'PY_JOBS'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
if root.is_dir():
  for d in sorted(root.iterdir(), reverse=True):
    if not d.is_dir(): continue
    try: m=json.loads((d/'manifest.json').read_text())
    except Exception: m={{}}
    try: s=json.loads((d/'status.json').read_text())
    except Exception: s={{}}
    print(json.dumps({{"job_id":d.name,"command":m.get("command","?"),"state":s.get("state","?"),"exit_code":s.get("exit_code")}}))
PY_JOBS
''', capture=True, check=False)
    rows: Dict[str, dict] = {}
    if result.returncode == 0:
        for line in (result.stdout or "").splitlines():
            try: row = json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(row, dict) and row.get("job_id"): rows[str(row["job_id"])] = row
    local_root = state_dir(cfg)
    if local_root.is_dir():
        for d in local_root.iterdir():
            if not d.is_dir() or d.name in rows: continue
            m, s = read_json(d / "manifest.json", default={}), read_json(d / "status.json", default={})
            if isinstance(m, dict) and isinstance(s, dict): rows[d.name] = {"job_id": d.name, "command": m.get("command","?"), "state": s.get("state","?"), "exit_code": s.get("exit_code")}
    print(f"{'JOB':<25} {'COMMAND':<18} {'STATE':<20} EXIT")
    for jid in sorted(rows, reverse=True):
        row=rows[jid]; code="-" if row.get("exit_code") is None else str(row.get("exit_code"))
        print(f"{jid:<25} {str(row.get('command','?')):<18} {str(row.get('state','?')):<20} {code}")
    return 0


def status(jid: str, cfg: dict) -> int:
    remote = remote_status(jid, cfg, allow_missing=True)
    if remote is not None: sync_metadata(jid, cfg); st=remote
    else: st=local_status(jid, cfg)
    if st is None: raise SlError(f"job not found: {jid}")
    manifest=load_manifest(jid, cfg)
    print(f"job:       {jid}\ncommand:   {manifest.get('command','?')}\nstate:     {st.get('state','?')}\nexit:      {st.get('exit_code') if st.get('exit_code') is not None else '-'}\ncreated:   {manifest.get('created_at','-')}\noutput:    {manifest.get('local_output_dir','-')}")
    memory = manifest.get("memory") if isinstance(manifest.get("memory"), dict) else {}
    required = st.get("memory_required_mib") if isinstance(st.get("memory_required_mib"), int) else memory.get("required_mib")
    free = st.get("memory_free_mib") if isinstance(st.get("memory_free_mib"), int) else None
    if isinstance(required, int): print(f"memory:    {format_memory_mib(free) if free is not None else 'unknown'} free / {format_memory_mib(required)} required")
    return 0


def logs(jid: str, cfg: dict, *, follow: bool) -> int:
    jid=validate_job_id(jid)
    if follow:
        rc=follow_remote_log(jid,cfg,lines="+1",follow=True); sync_metadata(jid,cfg); return rc
    result=ssh(f"cat {shlex.quote(remote_job_dir(jid,cfg) + '/job.log')}\n",capture=True,check=False)
    if result.returncode==0: sys.stdout.write(result.stdout or ""); sync_metadata(jid,cfg); return 0
    local=local_job_dir(jid,cfg)/"job.log"
    if local.is_file(): sys.stdout.write(local.read_text(encoding="utf-8",errors="replace")); return 0
    raise SlError(f"log unavailable for job {jid}")


def tail(jid: str, cfg: dict, *, lines: int, follow: bool) -> int:
    jid=validate_job_id(jid); rc=follow_remote_log(jid,cfg,lines=str(lines),follow=follow)
    if rc==0: sync_metadata(jid,cfg); return 0
    local=local_job_dir(jid,cfg)/"job.log"
    if local.is_file() and not follow:
        text=local.read_text(encoding="utf-8",errors="replace").splitlines(); print("\n".join(text[-lines:])); return 0
    return rc


def commands(cfg: dict) -> int:
    found: Dict[str, CommandSpec] = {}
    for directory in command_dirs(cfg):
        if directory.is_dir():
            for path in sorted(directory.glob("*.cmd")):
                try: spec=parse_command(path)
                except SlError as exc: warn(str(exc)); continue
                found.setdefault(spec.name,spec)
    print(f"{'COMMAND':<18} {'INPUTS':<10} {'OUTPUTS':<10} {'MEMCHECK':<12} DESCRIPTION")
    for name in sorted(found):
        spec=found[name]; inputs=','.join(map(str,spec.inputs)) or '-'; outputs=','.join(map(str,spec.outputs)) or '-'; mem=spec.memcheck_default or ('yes' if spec.memcheck else '-')
        print(f"{name:<18} {inputs:<10} {outputs:<10} {mem:<12} {spec.description}")
    return 0


def command_show(name: str, cfg: dict) -> int:
    spec=find_command(name,cfg)
    print(f"name:          {spec.name}\nfile:          {spec.path}\ndescription:   {spec.description or '-'}\ninputs:        {', '.join(map(str,spec.inputs)) or '-'}\noutputs:       {', '.join(map(str,spec.outputs)) or '-'}\nsetup version: {spec.setup_version}\nmemcheck:      {spec.memcheck_default or ('enabled (no default)' if spec.memcheck else '-')}")
    print("\n--- command ---"); print(spec.text,end="" if spec.text.endswith("\n") else "\n"); return 0
