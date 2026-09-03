from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Sequence

from . import targets


def _error(message: str, code: int = 1) -> int:
    print(f"[sl] ERROR: {message}", file=sys.stderr)
    return code


def _target_command(argv: Sequence[str]) -> int:
    args = list(argv)
    cfg = targets.read_vcp_config()
    if len(args) == 1:
        active = targets.active_target(cfg)
        if active:
            print(active)
        elif isinstance(cfg.get("ssh"), list) and cfg.get("ssh"):
            print("<legacy/default>")
        else:
            print("<none>")
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


def _configure_active_named_ssh(argv: Sequence[str]) -> int | None:
    """Handle `sl config ssh ...` when a named target is active.

    The legacy SL implementation writes directly to VCP's top-level `ssh` key.
    For named targets, update the active entry instead and still run the normal
    worker bootstrap against that newly configured endpoint.
    """
    args = list(argv)
    if len(args) < 2 or args[:2] != ["config", "ssh"]:
        return None
    cfg = targets.read_vcp_config()
    active = targets.active_target(cfg)
    if not active:
        return None

    ssh_args = args[2:]
    selected = targets.configure_active_ssh(ssh_args)
    assert selected == active
    shown_args = list(ssh_args[1:]) if ssh_args and ssh_args[0] == "--" else list(ssh_args)
    print(f"saved SSH target {active}: {shlex.join(shown_args)}")

    projection = targets.create_projection(active)
    old_vcp = os.environ.get("VCP_CONFIG")
    old_target = os.environ.get("SL_TARGET_NAME")
    os.environ["VCP_CONFIG"] = str(projection)
    os.environ["SL_TARGET_NAME"] = active
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
    return 0


def entrypoint(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    projection: Path | None = None
    old_vcp = os.environ.get("VCP_CONFIG")
    old_target = os.environ.get("SL_TARGET_NAME")

    try:
        if args and args[0] in {"targets", "list-targets"}:
            if len(args) != 1:
                raise targets.TargetError("usage: sl targets")
            return targets.print_targets()
        if args and args[0] == "target":
            return _target_command(args)

        configured = _configure_active_named_ssh(args)
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
