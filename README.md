# podlets

`podlets` makes an already-running RunPod/Vast GPU worker feel like disposable compute.

Phase 1 is deliberately **throwaway-first**: point `sl` at a command, stage local inputs through `vcp`, execute durably on the pod, fetch declared outputs, retain lightweight logs/metadata, and clean the heavy remote job workspace after success.

Persistent workspaces, snapshots, resume, and provider-managed `slr` podlets are intentionally deferred until this simple layer has been used in anger.

## Requirements

On the controller machine:

- Python 3.10+
- `ssh`
- `vcp` from [`markwelshboy/pod-runtime`](https://github.com/markwelshboy/pod-runtime)
- `HF_TOKEN` in the controller environment

The controller token is authoritative for ordinary Podlets use. A rented worker does **not** need `HF_TOKEN` baked into its template: `vcp` injects the controller token for each transfer and `sl` injects it into each detached workload. The minimal Podlets bootstrap does not persist the token on the worker.

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

`sl config ssh` writes the same `~/.config/vcp/config.json` SSH target used by `vcp`, verifies/bootstrap the worker immediately, and removes the old requirement to run a separate pod provisioning step before ordinary Podlets jobs. Normal `sl run` also performs an idempotent bootstrap check after local command/output validation and before staging inputs.

To explicitly repair or bootstrap the currently configured worker:

```bash
sl bootstrap
```

`sl` discovers the local `vcp` launcher from `SL_VCP`, `sl config vcp PATH`, `$PATH`, a sibling `../pod-runtime/vcp`, or `~/git/pod-runtime/vcp`.

## Multiple named pod targets

`sl` and `vcp` share the named target registry in `~/.config/vcp/config.json`. This lets several rented pods coexist without repeatedly replacing one global SSH endpoint.

Create targets with VCP or automatically with `rent-pod --name NAME --vcp`:

```bash
vcp config l40development ssh -i ~/.ssh/id_ed25519_runpod -p 12234 root@HOST1
vcp config rtx6000comfy ssh -i ~/.ssh/id_ed25519_runpod -p 13345 root@HOST2
```

List or select them from either tool:

```bash
sl targets
sl target l40development

vcp targets
vcp target l40development
```

The active target is shared, so `vcp target l40development` also changes the default target used by the next `sl` command, and vice versa.

Use a one-shot target without changing the active target:

```bash
sl --target rtx6000comfy run upscale INPUT OUTPUT --output-dir RESULTS
vcp --target rtx6000comfy r:/workspace/report.txt .
```

When a new SL job is created on a named target, the target name is stored in the local job manifest. Later commands automatically route back to that same pod even if you have switched the global active target in the meantime:

```bash
sl target l40development
sl --target rtx6000comfy run --detach upscale INPUT OUTPUT --output-dir RESULTS

# Active target can change afterward...
sl target l40development

# ...but this job still routes to rtx6000comfy from its recorded manifest.
sl status JOB_ID
sl tail JOB_ID
sl fetch JOB_ID
```

For safety, explicitly forcing a different target for a job already bound to another target is rejected instead of silently querying the wrong pod.

If a named target is active, `sl config ssh ...` updates that named target's SSH endpoint. If no named target exists, the previous single-endpoint `~/.config/vcp/config.json` format remains fully supported.

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

## Podcrumb application catalog

If [`podcrumbs`](https://github.com/markwelshboy/podcrumbs) is checked out as a sibling of Podlets or at `~/git/podcrumbs`, its `commands/` catalog is discovered automatically after built-in commands. An explicit `sl config command-dir PATH` still has highest precedence.

Podcrumb commands may declare `# sl:app APP`. That enables controller-side inspection without starting or provisioning a GPU job:

```bash
sl command help bg-remove
sl command controls bg-remove
sl command config bg-remove
sl command show bg-remove
```

These views are deliberately different:

- `help` renders the declared user-facing controls and defaults.
- `controls` prints the app's `controls.yaml` declaration.
- `config` prints its structural `config.yaml` implementation/capabilities.
- `show` prints the low-level `.cmd` adapter.

Podlets does not require PyYAML for this inspection path; the public controls contract intentionally uses a small flat YAML shape while structural configuration remains opaque to Podlets.

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
sl targets
sl target [NAME]
sl --target NAME run ...
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
sl command help COMMAND
sl command controls COMMAND
sl command config COMMAND
sl command show COMMAND
sl gpu
sl doctor
sl config ssh [ssh options] user@host
sl config ...
```

See [`docs/phase1.md`](docs/phase1.md) for the execution model and command-file contract.
