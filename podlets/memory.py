from __future__ import annotations

import math
import re

from .common import SlError, ssh

MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(m|mb|mib|g|gb|gib)\s*$", re.IGNORECASE)


def parse_memory_mib(value: str) -> int:
    match = MEMORY_RE.fullmatch(value)
    if not match:
        raise SlError("memory must include M or G units, e.g. --mem 18000M or --mem 18G")
    amount = float(match.group(1))
    if amount <= 0:
        raise SlError("memory requirement must be greater than zero")
    unit = match.group(2).lower()
    mib = amount * 1024 if unit.startswith("g") else amount
    result = int(math.ceil(mib))
    if result <= 0:
        raise SlError("memory requirement must be greater than zero")
    return result


def format_memory_mib(value: int | None) -> str:
    if value is None:
        return "-"
    if value >= 1024 and value % 1024 == 0:
        return f"{value // 1024} GiB"
    if value >= 1024:
        return f"{value / 1024:.1f} GiB"
    return f"{value} MiB"


def remote_gpu_memory() -> tuple[int, int]:
    script = """set -euo pipefail
command -v nvidia-smi >/dev/null 2>&1
nvidia-smi --query-gpu=memory.total,memory.free --format=csv,noheader,nounits | head -n1
"""
    result = ssh(script, capture=True)
    line = (result.stdout or "").strip().splitlines()[-1]
    try:
        total_s, free_s = [part.strip() for part in line.split(",", 1)]
        return int(total_s), int(free_s)
    except Exception as exc:
        raise SlError(f"could not parse remote GPU memory from nvidia-smi: {line!r}") from exc


def preflight_capacity(required_mib: int) -> tuple[int, int]:
    total, free = remote_gpu_memory()
    if required_mib > total:
        raise SlError(
            f"requested memory {format_memory_mib(required_mib)} exceeds GPU total {format_memory_mib(total)}"
        )
    return total, free
