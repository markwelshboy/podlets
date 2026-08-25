from __future__ import annotations

import shlex

from .common import SlError, info, remote_root, runtime_ref, runtime_repo, ssh


def _runtime_candidates(cfg: dict) -> list[str]:
    private = f"{remote_root(cfg)}/runtime/pod-runtime"
    return [
        private,
        "/workspace/pod-runtime",
        "/workspace/pod_runtime",
        "/opt/pod-runtime",
        "/opt/pod_runtime",
        "/workspace/git/pod-runtime",
        "/workspace/git/pod_runtime",
    ]


def build_bootstrap_script(cfg: dict) -> str:
    private = _runtime_candidates(cfg)[0]
    candidates = " ".join(shlex.quote(path) for path in _runtime_candidates(cfg))
    repo = shlex.quote(runtime_repo(cfg))
    ref = shlex.quote(runtime_ref(cfg))
    return f'''set -euo pipefail
runtime={shlex.quote(private)}
repo={repo}
ref={ref}

for cand in {candidates}; do
  if [[ -f "$cand/helpers_shell.sh" ]]; then
    printf 'existing\\t%s\\n' "$cand"
    exit 0
  fi
done

packages=()
command -v git >/dev/null 2>&1 || packages+=(git ca-certificates)
if ! command -v python3 >/dev/null 2>&1; then
  packages+=(python3 python3-venv)
else
  probe="$(mktemp -d /tmp/sl-venv-probe.XXXXXX)"
  if ! python3 -m venv "$probe" >/dev/null 2>&1; then packages+=(python3-venv); fi
  rm -rf "$probe"
fi

if (( ${{#packages[@]}} )); then
  if [[ "$(id -u)" != "0" || ! -x "$(command -v apt-get || true)" ]]; then
    echo "[sl] ERROR: worker bootstrap needs: ${{packages[*]}}; connect as root on an apt-based image or install them first" >&2
    exit 127
  fi
  echo "[sl] installing worker bootstrap prerequisites: ${{packages[*]}}" >&2
  apt-get update -y >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${{packages[@]}}" >/dev/null
fi

command -v git >/dev/null 2>&1 || {{ echo '[sl] ERROR: git unavailable after bootstrap' >&2; exit 127; }}
command -v python3 >/dev/null 2>&1 || {{ echo '[sl] ERROR: python3 unavailable after bootstrap' >&2; exit 127; }}
probe="$(mktemp -d /tmp/sl-venv-probe.XXXXXX)"
if ! python3 -m venv "$probe" >/dev/null 2>&1; then
  rm -rf "$probe"
  echo '[sl] ERROR: python3 venv support unavailable after bootstrap' >&2
  exit 127
fi
rm -rf "$probe"

mkdir -p "$(dirname "$runtime")"
if [[ -d "$runtime/.git" ]]; then
  git -C "$runtime" fetch --quiet --depth 1 --no-tags origin "$ref"
else
  rm -rf "$runtime"
  git clone --quiet --depth 1 --no-tags "$repo" "$runtime"
  git -C "$runtime" fetch --quiet --depth 1 --no-tags origin "$ref"
fi
git -C "$runtime" checkout --quiet --detach FETCH_HEAD
[[ -f "$runtime/helpers_shell.sh" ]] || {{ echo '[sl] ERROR: bootstrapped pod-runtime is missing helpers_shell.sh' >&2; exit 127; }}
printf 'bootstrapped\\t%s\\n' "$runtime"
'''


def ensure_worker_runtime(cfg: dict, *, announce: bool = False) -> str:
    result = ssh(build_bootstrap_script(cfg), capture=True, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SlError(f"worker bootstrap failed: {detail or 'unknown error'}")
    line = (result.stdout or "").strip().splitlines()
    if not line:
        raise SlError("worker bootstrap returned no runtime path")
    fields = line[-1].split("\t", 1)
    if len(fields) != 2 or fields[0] not in {"existing", "bootstrapped"}:
        raise SlError(f"worker bootstrap returned unexpected response: {line[-1]!r}")
    state, path = fields
    if announce or state == "bootstrapped":
        info(f"worker runtime: {state} {path}")
    return path


def probe_worker_runtime(cfg: dict) -> str | None:
    candidates = " ".join(shlex.quote(path) for path in _runtime_candidates(cfg))
    result = ssh(
        f'''for cand in {candidates}; do
  if [[ -f "$cand/helpers_shell.sh" ]]; then printf '%s\\n' "$cand"; exit 0; fi
done
exit 1
''',
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip().splitlines()
    return value[-1] if value else None
