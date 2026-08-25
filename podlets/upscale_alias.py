from __future__ import annotations

import subprocess
import sys
from typing import Sequence

from .cli import parse_run
from .common import SlError
from .jobs import run_job


def parse_upscale(argv: Sequence[str]):
    """Parse `sl upscale` exactly like `sl run`, with a fixed command alias.

    Everything before `--` is controller-side Podlets syntax. Everything after
    `--` is forwarded unchanged to the remote `upscale` command.
    """
    return parse_run(argv, command_alias="upscale")


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
