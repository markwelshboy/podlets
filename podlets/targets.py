from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
JOB_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{8}$")
JOB_BOUND_COMMANDS = {"status", "logs", "tail", "cancel", "fetch", "clean", "purge"}
DEFAULT_VCP_CONFIG = "~/.config/vcp/config.json"
DEFAULT_SL_CONFIG = "~/.config/sl/config.json"
DEFAULT_STATE_DIR = "~/.local/state/sl/jobs"

SSH_OPTIONS_WITH_VALUE = {
    "-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J",
    "-L", "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w",
}


class TargetError(RuntimeError):
    pass


def real_vcp_config_path(environ: Mapping[str, str] | None = None) -> Path:
    env = environ or os.environ
    return Path(env.get("VCP_CONFIG", DEFAULT_VCP_CONFIG)).expanduser()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TargetError(f"could not read {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def read_vcp_config(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    path = real_vcp_config_path(environ)
    value = read_json(path, {})
    if not isinstance(value, dict):
        raise TargetError(f"invalid VCP config {path}: expected JSON object")
    return value


def validate_target_name(name: str) -> str:
    value = name.strip()
    if not value or not TARGET_RE.fullmatch(value):
        raise TargetError(
            "target names may contain letters, numbers, dot, underscore, and hyphen"
        )
    return value


def target_entries(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = cfg.get("targets")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TargetError("invalid VCP config: targets must be an object")
    return {
        str(name): value
        for name, value in raw.items()
        if isinstance(name, str) and isinstance(value, dict)
    }


def active_target(cfg: dict[str, Any]) -> str | None:
    value = cfg.get("active_target")
    return value if isinstance(value, str) and value else None


def target_entry(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    name = validate_target_name(name)
    entry = target_entries(cfg).get(name)
    if entry is None:
        raise TargetError(f"unknown VCP target: {name}")
    ssh = entry.get("ssh")
    if not isinstance(ssh, list) or not ssh or not all(isinstance(x, str) and x for x in ssh):
        raise TargetError(f"VCP target {name!r} has no valid SSH configuration")
    return entry


def split_ssh_args(value: Any) -> tuple[list[str], str]:
    if not isinstance(value, list) or not value or not all(isinstance(x, str) and x for x in value):
        raise TargetError("SSH arguments must be a non-empty list of strings")
    args = list(value)
    options: list[str] = []
    destination: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-") and arg != "-":
            options.append(arg)
            if arg in SSH_OPTIONS_WITH_VALUE:
                if i + 1 >= len(args):
                    raise TargetError(f"SSH option {arg} requires a value")
                options.append(args[i + 1])
                i += 2
            else:
                i += 1
            continue
        if destination is not None:
            raise TargetError("VCP SSH configuration must contain exactly one destination")
        destination = arg
        i += 1
    if not destination:
        raise TargetError("SSH configuration has no destination")
    return options, destination


def normalize_ssh_args(value: Any) -> list[str]:
    options, destination = split_ssh_args(value)
    return [*options, destination]


def set_active_target(name: str, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    cfg = read_vcp_config(environ)
    name = validate_target_name(name)
    entry = target_entry(cfg, name)
    cfg["active_target"] = name
    cfg["version"] = max(int(cfg.get("version") or 1), 2)
    cfg.pop("ssh", None)
    write_json(real_vcp_config_path(environ), cfg)
    return entry


def endpoint_from_ssh(value: Any) -> str:
    try:
        options, destination = split_ssh_args(value)
    except TargetError:
        return "-"
    port = None
    i = 0
    while i < len(options):
        arg = options[i]
        if arg == "-p" and i + 1 < len(options):
            port = options[i + 1]
            i += 2
            continue
        if arg.startswith("-p") and len(arg) > 2:
            port = arg[2:]
        i += 1
    return f"{destination}:{port}" if port else destination


def print_targets(environ: Mapping[str, str] | None = None) -> int:
    cfg = read_vcp_config(environ)
    entries = target_entries(cfg)
    active = active_target(cfg)
    print(f"VCP config:    {real_vcp_config_path(environ)}")
    print(f"Active target: {active or '<none>'}")
    if not entries:
        print("Targets:       <none>")
        return 0
    print()
    print(f"{'NAME':<32} {'ACTIVE':<7} {'POD ID':<20} {'ENDPOINT':<36} DESCRIPTION")
    print("-" * 108)
    for name, entry in sorted(entries.items()):
        marker = "yes" if name == active else ""
        pod_id = str(entry.get("pod_id") or "-")
        endpoint = endpoint_from_ssh(entry.get("ssh"))
        description = str(entry.get("description") or "")
        print(f"{name:<32.32} {marker:<7.7} {pod_id:<20.20} {endpoint:<36.36} {description}")
    return 0


def consume_global_target(argv: Sequence[str]) -> tuple[list[str], str | None]:
    args = list(argv)
    if not args:
        return args, None
    first = args[0]
    if first == "--target":
        if len(args) < 2:
            raise TargetError("--target requires a target name")
        return args[2:], validate_target_name(args[1])
    if first.startswith("--target="):
        return args[1:], validate_target_name(first.split("=", 1)[1])
    return args, None


def _sl_state_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = environ or os.environ
    if env.get("SL_STATE_DIR"):
        return Path(env["SL_STATE_DIR"]).expanduser()
    cfg_path = Path(env.get("SL_CONFIG", DEFAULT_SL_CONFIG)).expanduser()
    cfg = read_json(cfg_path, {})
    if isinstance(cfg, dict) and isinstance(cfg.get("state_dir"), str) and cfg.get("state_dir"):
        return Path(str(cfg["state_dir"])).expanduser()
    return Path(DEFAULT_STATE_DIR).expanduser()


def job_id_from_argv(argv: Sequence[str]) -> str | None:
    args = list(argv)
    if not args or args[0] not in JOB_BOUND_COMMANDS:
        return None
    for arg in args[1:]:
        if JOB_ID_RE.fullmatch(arg):
            return arg
    return None


def job_target(argv: Sequence[str], environ: Mapping[str, str] | None = None) -> str | None:
    jid = job_id_from_argv(argv)
    if not jid:
        return None
    manifest = _sl_state_dir(environ) / jid / "manifest.json"
    value = read_json(manifest, {})
    if not isinstance(value, dict):
        return None
    target = value.get("target")
    return validate_target_name(target) if isinstance(target, str) and target else None


def selected_target(
    argv: Sequence[str],
    explicit: str | None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    if explicit:
        return validate_target_name(explicit)
    recorded = job_target(argv, environ)
    if recorded:
        return recorded
    return active_target(read_vcp_config(environ))


def create_projection(
    name: str,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Create a temporary compatibility config for exactly one named target.

    SL's older transport helpers still read top-level ``ssh`` directly. Keep
    that field only in the temporary projection, while also projecting the one
    named target as active so the target-aware VCP launcher resolves the same
    destination instead of falling back to persistent default state.
    """
    env = environ or os.environ
    cfg = read_vcp_config(env)
    entry = dict(target_entry(cfg, name))
    entry["ssh"] = normalize_ssh_args(entry["ssh"])

    projection = dict(cfg)
    projection["active_target"] = name
    projection["targets"] = {name: entry}
    projection["ssh"] = list(entry["ssh"])

    cache_root = Path(env.get("SL_TARGET_CACHE", "~/.cache/sl/targets")).expanduser()
    cache_root.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f"{name}-", suffix=".json", dir=cache_root)
    os.close(fd)
    path = Path(raw_path)
    path.write_text(json.dumps(projection, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def configure_active_ssh(
    ssh_args: Sequence[str],
    environ: Mapping[str, str] | None = None,
) -> str:
    """Update the active named target; unnamed/default SSH state is unsupported."""
    args = [str(x) for x in ssh_args]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        raise TargetError("usage: sl config ssh [ssh options] user@host")
    cfg = read_vcp_config(environ)
    name = active_target(cfg)
    if not name:
        raise TargetError(
            "no active VCP target; configure/discover one with 'vcp config ssh ...' "
            "or select one with 'sl target NAME'"
        )
    entry = target_entry(cfg, name)
    entry["ssh"] = normalize_ssh_args(args)
    raw = cfg.get("targets")
    assert isinstance(raw, dict)
    raw[name] = entry
    cfg.pop("ssh", None)
    write_json(real_vcp_config_path(environ), cfg)
    return name
