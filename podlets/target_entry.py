from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from . import targets


def _error(message: str, code: int = 1) -> int:
    print(f"[sl] ERROR: {message}", file=sys.stderr)
    return code


def _usage() -> str:
    from .cli import usage

    return (
        usage().rstrip()
        + "\n\nNamed targets:\n"
        + "  sl targets\n"
        + "  sl target [NAME]\n"
        + "  sl config ssh [ssh options] user@host\n"
        + "  sl config NAME ssh [ssh options] user@host\n"
        + "  sl --target NAME <command...>\n\n"
        + "The active target is shared with vcp. SSH target registration is delegated\n"
        + "to vcp, and new jobs remember their target so later status/logs/fetch/cancel\n"
        + "operations route back to the same pod.\n"
    )


def _target_command(argv: Sequence[str]) -> int:
    args = list(argv)
    cfg = targets.read_vcp_config()
    if len(args) == 1:
        print(targets.active_target(cfg) or "<none>")
        return 0
    if len(args) != 2:
        raise targets.TargetError("usage: sl target [NAME]")
    name = targets.validate_target_name(args[1])
    entry = targets.set_active_target(name)
    print(f"[sl] Active target: {name} ({targets.endpoint_from_ssh(entry.get('ssh'))})")
    return 0


def _patch_manifest_target() -> None:
    """Teach new job manifests to remember whichever target this process uses."""
    from . import spec

    original = spec.manifest_for_job
    if getattr(original, "_sl_target_wrapped", False):
        return

    def wrapped(*args, **kwargs):
        manifest = original(*args, **kwargs)
        target_name = os.environ.get("SL_TARGET_NAME")
        if target_name:
            manifest["target"] = target_name
        return manifest

    wrapped._sl_target_wrapped = True  # type: ignore[attr-defined]
    spec.manifest_for_job = wrapped


def _bootstrap_target(name: str) -> None:
    """Bootstrap SL worker runtime against one already-configured named target."""
    projection = targets.create_projection(name)
    old_vcp = os.environ.get("VCP_CONFIG")
    old_target = os.environ.get("SL_TARGET_NAME")
    os.environ["VCP_CONFIG"] = str(projection)
    os.environ["SL_TARGET_NAME"] = name
    try:
        from .bootstrap import ensure_worker_runtime
        from .common import sl_config

        ensure_worker_runtime(sl_config(), announce=True)
    finally:
        try:
            projection.unlink()
        except FileNotFoundError:
            pass
        if old_vcp is None:
            os.environ.pop("VCP_CONFIG", None)
        else:
            os.environ["VCP_CONFIG"] = old_vcp
        if old_target is None:
            os.environ.pop("SL_TARGET_NAME", None)
        else:
            os.environ["SL_TARGET_NAME"] = old_target


def _configure_ssh_via_vcp(argv: Sequence[str]) -> int | None:
    """Delegate SL SSH target registration to VCP's canonical target logic.

    Bare ``sl config ssh ...`` gets the same RunPod endpoint/name discovery as
    ``vcp config ssh ...``. Named ``sl config NAME ssh ...`` saves that target
    through VCP, activates it, then bootstraps the SL worker runtime.
    """
    args = list(argv)
    if not args or args[0] != "config":
        return None

    named: str | None = None
    if len(args) >= 2 and args[1] == "ssh":
        pass
    elif len(args) >= 3 and args[2] == "ssh":
        named = targets.validate_target_name(args[1])
    else:
        return None

    from .common import sl_config, vcp_path

    command = [str(vcp_path(sl_config())), *args]
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        return int(result.returncode)

    if named is not None:
        targets.set_active_target(named)
        active = named
        print(f"[sl] Active target: {active}")
    else:
        active = targets.active_target(targets.read_vcp_config())
        if not active:
            raise targets.TargetError(
                "vcp SSH configuration succeeded without producing an active named target"
            )

    _bootstrap_target(active)
    return 0


def entrypoint(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    projection: Path | None = None
    old_vcp = os.environ.get("VCP_CONFIG")
    old_target = os.environ.get("SL_TARGET_NAME")

    try:
        if not args or args[0] in {"-h", "--help", "help"}:
            print(_usage())
            return 0
        if args[0] in {"targets", "list-targets"}:
            if len(args) != 1:
                raise targets.TargetError("usage: sl targets")
            return targets.print_targets()
        if args[0] == "target":
            return _target_command(args)

        configured = _configure_ssh_via_vcp(args)
        if configured is not None:
            return configured

        forwarded, explicit = targets.consume_global_target(args)
        recorded = targets.job_target(forwarded)
        if explicit and recorded and explicit != recorded:
            jid = targets.job_id_from_argv(forwarded) or "job"
            raise targets.TargetError(
                f"{jid} belongs to target {recorded!r}; refusing explicit target {explicit!r}"
            )
        selected = explicit or recorded or targets.active_target(targets.read_vcp_config())
        if selected:
            # Validates existence and SSH configuration before any remote work.
            projection = targets.create_projection(selected)
            os.environ["VCP_CONFIG"] = str(projection)
            os.environ["SL_TARGET_NAME"] = selected
            print(f"[sl] target: {selected}", file=sys.stderr)

        _patch_manifest_target()
        sys.argv = [sys.argv[0], *forwarded]

        from .cli import entrypoint as cli_entrypoint

        return cli_entrypoint()
    except targets.TargetError as exc:
        return _error(str(exc), 1)
    finally:
        if projection is not None:
            try:
                projection.unlink()
            except FileNotFoundError:
                pass
        if old_vcp is None:
            os.environ.pop("VCP_CONFIG", None)
        else:
            os.environ["VCP_CONFIG"] = old_vcp
        if old_target is None:
            os.environ.pop("SL_TARGET_NAME", None)
        else:
            os.environ["SL_TARGET_NAME"] = old_target
