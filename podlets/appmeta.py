from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .common import SlError
from .spec import CommandSpec, find_command


def _app_name(spec: CommandSpec) -> str:
    values = spec.directives.get("app", [])
    if not values:
        raise SlError(f"command {spec.name} does not declare '# sl:app APP'")
    if len(values) != 1 or not values[0]:
        raise SlError(f"command {spec.name}: sl:app must be declared exactly once")
    return values[0]


def app_dir(spec: CommandSpec) -> Path:
    app = _app_name(spec)
    root = spec.path.parent.parent
    path = root / "apps" / app
    if not path.is_dir():
        raise SlError(f"command {spec.name}: app directory not found: {path}")
    return path


def _parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if not value:
        return ""
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"null", "none", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    if re.fullmatch(r"-?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)", value):
        return float(value)
    return value


def load_controls(path: Path) -> OrderedDict[str, dict[str, Any]]:
    if not path.is_file():
        raise SlError(f"controls file not found: {path}")
    controls: OrderedDict[str, dict[str, Any]] = OrderedDict()
    in_controls = False
    current: str | None = None
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if indent == 0:
            in_controls = line == "controls:"
            current = None
            continue
        if not in_controls:
            continue
        if indent == 2 and line.endswith(":"):
            current = line[:-1].strip()
            if not current:
                raise SlError(f"invalid controls entry at {path}:{number}")
            controls[current] = {}
            continue
        if indent == 4 and current and ":" in line:
            key, value = line.split(":", 1)
            controls[current][key.strip()] = _parse_scalar(value)
            continue
        raise SlError(
            f"unsupported controls.yaml syntax at {path}:{number}; "
            "Podlets metadata uses flat control mappings"
        )
    if not controls:
        raise SlError(f"no controls declared in {path}")
    return controls


def _app_title(path: Path, fallback: str) -> str:
    app_yaml = path / "app.yaml"
    if app_yaml.is_file():
        for raw in app_yaml.read_text(encoding="utf-8").splitlines():
            if raw.startswith("title:"):
                value = raw.split(":", 1)[1].strip().strip('"\'')
                if value:
                    return value
    return fallback


def _format_default(value: Any) -> str:
    if value is None:
        return "none"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    return str(value)


def command_help(name: str, cfg: dict) -> int:
    spec = find_command(name, cfg)
    root = app_dir(spec)
    controls = load_controls(root / "controls.yaml")
    title = _app_title(root, spec.description or spec.name)
    print(f"{spec.name} — {title}")
    print()
    print(f"Run: sl run {spec.name} <operands...> -- [controls]")
    print(f"Inputs:  {', '.join(map(str, spec.inputs)) or '-'}")
    print(f"Outputs: {', '.join(map(str, spec.outputs)) or '-'}")
    print()
    print("Controls:")
    for control, meta in controls.items():
        flag = str(meta.get("flag") or "")
        kind = str(meta.get("type") or "string")
        metavar = str(meta.get("metavar") or ("VALUE" if kind not in {"boolean"} else ""))
        suffix = ""
        if kind == "multi_choice":
            suffix = f" {metavar}..."
        elif kind != "boolean":
            suffix = f" {metavar}"
        negative = meta.get("negative_flag")
        flags = flag + suffix
        if negative:
            flags += f" / {negative}"
        print(f"  {flags}")
        help_text = meta.get("help")
        if help_text:
            print(f"      {help_text}")
        choices = meta.get("choices")
        if isinstance(choices, list):
            print(f"      Choices: {', '.join(str(x) for x in choices)}")
        print(f"      Default: {_format_default(meta.get('default'))}")
        if meta.get("minimum") is not None or meta.get("maximum") is not None:
            bounds = []
            if meta.get("minimum") is not None:
                bounds.append(f">= {meta['minimum']}")
            if meta.get("maximum") is not None:
                bounds.append(f"<= {meta['maximum']}")
            print(f"      Range: {'; '.join(bounds)}")
        print()
    return 0


def _show_file(name: str, cfg: dict, filename: str) -> int:
    spec = find_command(name, cfg)
    path = app_dir(spec) / filename
    if not path.is_file():
        raise SlError(f"command {spec.name}: {filename} not found: {path}")
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def command_controls(name: str, cfg: dict) -> int:
    return _show_file(name, cfg, "controls.yaml")


def command_config(name: str, cfg: dict) -> int:
    return _show_file(name, cfg, "config.yaml")
