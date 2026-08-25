# podlets

`podlets` makes an already-running RunPod/Vast GPU worker feel like disposable compute.

Phase 1 is deliberately **throwaway-first**: point `sl` at a command, stage local inputs through `vcp`, execute durably on the pod, fetch declared outputs, retain lightweight logs/metadata, and clean the heavy remote job workspace after success.

Persistent workspaces, snapshots, resume, and provider-managed `slr` podlets are intentionally deferred until this simple layer has been used in anger.

## Requirements

On the controller machine:

- Python 3.10+
- `ssh`
- `vcp` from [`markwelshboy/pod-runtime`](https://github.com/markwelshboy/pod-runtime)
- `HF_TOKEN` available when `vcp` needs it

A worker normally needs `bash`, `git`, `python3` with venv support, and `nvidia-smi`. `sl` can bootstrap `git`/Python venv support automatically on a root-accessible apt-based template, and installs its private `pod-runtime` under `/workspace/.sl/runtime/pod-runtime` when no existing runtime is available.

## Install

```bash
git clone https://github.com/markwelshboy/podlets.git ~/git/podlets
ln -sfn ~/git/podlets/sl ~/.local/bin/sl
hash -r
```

Configure a newly rented worker directly through `sl`:

```bash
sl config ssh -p 12345 root@HOST
sl doctor
sl commands
```

`sl config ssh` writes the same `~/.config/vcp/config.json` SSH target used by `vcp`, verifies/bootstrap the worker immediately, and removes the old requirement to run a separate pod provisioning step before ordinary Podlets jobs. Normal `sl run` also performs an idempotent bootstrap check before staging inputs.

To explicitly repair or bootstrap the currently configured worker:

```bash
sl bootstrap
```

`sl` discovers the local `vcp` launcher from `SL_VCP`, `sl config vcp PATH`, `$PATH`, a sibling `../pod-runtime/vcp`, or `~/git/pod-runtime/vcp`.

## First smoke test

```bash
mkdir -p /tmp/sl-in
printf 'hello from podlets\n' > /tmp/sl-in/hello.txt

sl run smoke /tmp/sl-in sl-smoke-out --output-dir /tmp -- --alpha "two words"

cat /tmp/sl-smoke-out/sl-smoke.txt
cat /tmp/sl-smoke-out/sl-in/hello.txt
```

Memory-gated smoke test:

```bash
sl run --detach --mem 18G smoke /tmp/sl-in sl-smoke-wait --output-dir /tmp
sl jobs
sl tail JOB_ID
```

## SeedVR2

```bash
sl run --mem 18G seedvr2 \
  ~/images/toprocess/ \
  seedvr2_out/ \
  --output-dir ~/results \
  -- \
  --config examples/lowlight-jpeg-naturalize.json \
  --seed 43
```

`--mem` means minimum **free GPU VRAM** before `sl_run` starts. A command must opt in with `# sl:memcheck`. Without `--mem` (and without a command-level default) the job starts normally.

## Job lifecycle controls

```bash
sl status JOB
sl tail JOB
sl cancel JOB
sl clean JOB
sl purge JOB
```

`sl cancel JOB` terminates the remote job process group and records the job as `CANCELLED`, while retaining logs, metadata, and the remote workspace for inspection. `sl clean JOB` can then discard only the heavy workspace, while `sl purge JOB` removes the retained job entirely.

## Phase 1 CLI

```text
sl run ...
sl bootstrap
sl jobs
sl status JOB
sl logs [-f] JOB
sl tail [-n N] [--no-follow] JOB
sl cancel JOB
sl fetch JOB
sl clean JOB
sl purge [--force] JOB
sl commands
sl command show COMMAND
sl gpu
sl doctor
sl config ssh [ssh options] user@host
sl config ...
```

See [`docs/phase1.md`](docs/phase1.md) for the execution model and command-file contract.
