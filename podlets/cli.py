from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence

from .common import SL_CONFIG_PATH, VCP_CONFIG_PATH, SlError, cleanup_policy, command_dirs, remote_job_dir, remote_root, runtime_ref, runtime_repo, sl_config, ssh, ssh_argv, state_dir, validate_job_id, vcp_path, write_sl_config
from .jobs import command_show, commands, jobs, logs, run_job, status, tail
from .memory import format_memory_mib, remote_gpu_memory
from .remote import clean_remote_job, fetch_outputs, local_status, mark_complete, purge_job, remote_status, sync_metadata


def config_command(argv: Sequence[str]) -> int:
    cfg=sl_config()
    if not argv or argv[0] in {"show","list"}:
        print(f"config:        {SL_CONFIG_PATH}")
        print(f"vcp config:    {VCP_CONFIG_PATH}")
        try: print(f"vcp:           {vcp_path(cfg)}")
        except SlError: print("vcp:           <not found>")
        try: print(f"ssh:           {shlex.join(ssh_argv())}")
        except SlError: print("ssh:           <not configured>")
        print(f"remote root:   {remote_root(cfg)}\ncommand dir:   {command_dirs(cfg)[0]}\nstate dir:     {state_dir(cfg)}\ncleanup:       {cleanup_policy(cfg)}\nruntime repo:  {runtime_repo(cfg)}\nruntime ref:   {runtime_ref(cfg)}")
        return 0
    action=argv[0]
    if action=="remote-root":
        if len(argv)!=2 or not argv[1].startswith("/") or argv[1]=="/": raise SlError("usage: sl config remote-root /absolute/path")
        cfg["remote_root"]=argv[1].rstrip("/")
    elif action=="command-dir":
        if len(argv)!=2: raise SlError("usage: sl config command-dir PATH")
        cfg["command_dir"]=str(Path(argv[1]).expanduser())
    elif action=="state-dir":
        if len(argv)!=2: raise SlError("usage: sl config state-dir PATH")
        cfg["state_dir"]=str(Path(argv[1]).expanduser())
    elif action=="cleanup":
        if len(argv)!=2 or argv[1] not in {"never","successful","always"}: raise SlError("usage: sl config cleanup never|successful|always")
        cfg["cleanup"]=argv[1]
    elif action=="runtime-repo":
        if len(argv)!=2: raise SlError("usage: sl config runtime-repo URL")
        cfg["runtime_repo"]=argv[1]
    elif action=="runtime-ref":
        if len(argv)!=2: raise SlError("usage: sl config runtime-ref REF")
        cfg["runtime_ref"]=argv[1]
    elif action=="vcp":
        if len(argv)!=2: raise SlError("usage: sl config vcp PATH")
        cfg["vcp"]=str(Path(argv[1]).expanduser())
    elif action=="clear":
        if SL_CONFIG_PATH.exists(): SL_CONFIG_PATH.unlink()
        print(f"removed config: {SL_CONFIG_PATH}"); return 0
    else: raise SlError(f"unknown config command: {action}")
    write_sl_config(cfg); return config_command(["show"])


def usage() -> str:
    return """sl — disposable GPU jobs on the vcp-configured pod

Usage:
  sl run [--detach] [--mem MEM] [--output-dir DIR] [--no-fetch] [--keep-remote] COMMAND <operands...> [-- <command args...>]
  sl --command COMMAND <operands...> [-- <command args...>]
  sl jobs
  sl status JOB
  sl logs [-f] JOB
  sl tail [-n N] [--no-follow] JOB
  sl fetch [--output-dir DIR] JOB
  sl clean JOB
  sl purge [--force] JOB
  sl commands
  sl command show COMMAND
  sl gpu
  sl doctor
  sl config [show|...]

Phase 1 is intentionally throwaway-first: stage inputs, execute durably, fetch outputs,
retain lightweight logs/metadata, and clean heavy remote job data after success.
"""


def split_run_argv(argv: Sequence[str]) -> tuple[List[str],List[str]]:
    argv=list(argv)
    if "--" in argv:
        i=argv.index("--"); return argv[:i],argv[i+1:]
    return argv,[]


def parse_run(argv: Sequence[str], *, command_alias: str|None=None) -> argparse.Namespace:
    front,extra=split_run_argv(argv)
    parser=argparse.ArgumentParser(prog="sl run")
    parser.add_argument("--detach",action="store_true")
    parser.add_argument("--mem")
    parser.add_argument("--output-dir",default=".")
    parser.add_argument("--no-fetch",action="store_true")
    parser.add_argument("--keep-remote",action="store_true")
    if command_alias is None: parser.add_argument("command")
    parser.add_argument("operands",nargs="*")
    ns=parser.parse_args(front)
    if command_alias is not None: ns.command=command_alias
    ns.extra=extra
    return ns


def doctor() -> int:
    cfg=sl_config(); failures=0
    print("Podlets Phase 1 doctor")
    try: print(f"  vcp:        OK  {vcp_path(cfg)}")
    except SlError as exc: print(f"  vcp:        FAIL {exc}"); failures+=1
    try: print(f"  ssh config: OK  {shlex.join(ssh_argv())}")
    except SlError as exc: print(f"  ssh config: FAIL {exc}"); failures+=1; return 1
    result=ssh("printf 'ok\\n'\n",capture=True,check=False)
    if result.returncode==0: print("  ssh:        OK")
    else: print("  ssh:        FAIL"); failures+=1; return 1
    result=ssh("for x in bash git python3 nvidia-smi; do command -v \"$x\" >/dev/null 2>&1 || { echo \"missing:$x\"; exit 1; }; done; echo ok\n",capture=True,check=False)
    if result.returncode==0: print("  worker:     OK  bash/git/python3/nvidia-smi")
    else: print(f"  worker:     FAIL {(result.stdout or result.stderr or '').strip()}"); failures+=1
    try:
        total,free=remote_gpu_memory(); print(f"  GPU VRAM:   OK  {format_memory_mib(free)} free / {format_memory_mib(total)} total")
    except Exception as exc: print(f"  GPU VRAM:   FAIL {exc}"); failures+=1
    print(f"  runtime:    {runtime_repo(cfg)} @ {runtime_ref(cfg)}")
    return 1 if failures else 0


def gpu() -> int:
    total,free=remote_gpu_memory(); used=total-free
    print(f"GPU VRAM: {format_memory_mib(used)} used / {format_memory_mib(total)} total ({format_memory_mib(free)} free)")
    result=ssh("nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null || true\n",capture=True,check=False)
    procs=(result.stdout or "").strip()
    if procs: print("Processes:\n"+procs)
    return 0


def fetch_command(argv: Sequence[str]) -> int:
    parser=argparse.ArgumentParser(prog="sl fetch"); parser.add_argument("--output-dir"); parser.add_argument("job"); ns=parser.parse_args(argv)
    cfg=sl_config(); jid=validate_job_id(ns.job)
    fetch_outputs(jid,cfg,Path(ns.output_dir).expanduser() if ns.output_dir else None)
    st=remote_status(jid,cfg,allow_missing=True)
    if st and st.get("state")=="SUCCEEDED": mark_complete(jid,cfg)
    sync_metadata(jid,cfg)
    current=remote_status(jid,cfg,allow_missing=True) or local_status(jid,cfg) or {}
    policy=cleanup_policy(cfg)
    if policy=="always" or (policy=="successful" and current.get("state")=="COMPLETE"): clean_remote_job(jid,cfg)
    return 0


def main(argv: Sequence[str]|None=None) -> int:
    argv=list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in {"help","-h","--help"}: print(usage()); return 0
    cfg=sl_config()
    if argv[0]=="run": return run_job(parse_run(argv[1:]))
    if argv[0]=="--command":
        if len(argv)<2: raise SlError("usage: sl --command COMMAND <operands...> [-- <command args...>]")
        return run_job(parse_run(argv[2:],command_alias=argv[1]))
    if argv[0]=="jobs": return jobs(cfg)
    if argv[0]=="status" and len(argv)==2: return status(validate_job_id(argv[1]),cfg)
    if argv[0]=="logs":
        p=argparse.ArgumentParser(prog="sl logs"); p.add_argument("-f","--follow",action="store_true"); p.add_argument("job"); ns=p.parse_args(argv[1:]); return logs(ns.job,cfg,follow=ns.follow)
    if argv[0]=="tail":
        p=argparse.ArgumentParser(prog="sl tail"); p.add_argument("-n",type=int,default=100); p.add_argument("--no-follow",action="store_true"); p.add_argument("job"); ns=p.parse_args(argv[1:]); return tail(ns.job,cfg,lines=ns.n,follow=not ns.no_follow)
    if argv[0]=="fetch": return fetch_command(argv[1:])
    if argv[0]=="clean" and len(argv)==2: clean_remote_job(validate_job_id(argv[1]),cfg); sync_metadata(argv[1],cfg); return 0
    if argv[0]=="purge":
        p=argparse.ArgumentParser(prog="sl purge"); p.add_argument("--force",action="store_true"); p.add_argument("job"); ns=p.parse_args(argv[1:]); purge_job(validate_job_id(ns.job),cfg,force=ns.force); return 0
    if argv[0]=="commands": return commands(cfg)
    if argv[0:2]==["command","show"] and len(argv)==3: return command_show(argv[2],cfg)
    if argv[0]=="gpu": return gpu()
    if argv[0]=="doctor": return doctor()
    if argv[0]=="config": return config_command(argv[1:])
    raise SlError(f"unknown command; run 'sl --help': {shlex.join(argv)}")


def entrypoint() -> int:
    try: return main()
    except SlError as exc: return _error(str(exc),1)
    except subprocess.CalledProcessError as exc: return _error(f"command failed with exit status {exc.returncode}",exc.returncode or 1)
    except KeyboardInterrupt: return _error("interrupted",130)


def _error(message: str,code: int) -> int:
    print(f"[sl] ERROR: {message}",file=sys.stderr); return code
