from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Sequence

from .common import SlError
from .jobs import run_job


def parse_upscale(argv: Sequence[str]) -> argparse.Namespace:
    raw = list(argv)
    if len(raw) < 2 or raw[0].startswith("-") or raw[1].startswith("-"):
        raise SlError(
            "usage: sl upscale INPUT OUTPUT [job options] [model/upscale options]; "
            "INPUT and OUTPUT must come first"
        )

    parser = argparse.ArgumentParser(
        prog="sl upscale",
        description=(
            "Submit the generic bucket-aware upscale Podlet. Put INPUT and OUTPUT "
            "before model-specific options; unknown options are forwarded to the "
            "upscaler unchanged."
        ),
    )
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--detach", action="store_true")
    parser.add_argument("--mem")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--verbosity", choices=["none", "run", "debug", "full"])
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--keep-remote", action="store_true")

    ns, extra = parser.parse_known_args(raw)
    ns.command = "upscale"
    ns.operands = [ns.input, ns.output]
    ns.extra = extra
    return ns


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return run_job(parse_upscale(args))
    except SlError as exc:
        print(f"[sl] ERROR: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(
            f"[sl] ERROR: command failed with exit status {exc.returncode}",
            file=sys.stderr,
        )
        return exc.returncode or 1
    except KeyboardInterrupt:
        print("[sl] ERROR: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
