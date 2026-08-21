# Phase 1 — throwaway jobs

## Product rule

The primary Podlets workflow is intentionally boring:

```text
local input
  -> vcp/Hugging Face staging
  -> durable remote job
  -> output
  -> vcp/Hugging Face retrieval
  -> cleanup
```

A user should not need to understand workspaces, snapshots, hydration, or lineage to upscale a directory or run a short experiment.

Phase 1 keeps only cheap history locally and remotely: manifest, status, log, command snapshot, and runner. Heavy `input/`, `output/`, and `work/` data are cleaned after a successful fetched job by default.

## Relationship to pod-runtime

`podlets` owns the `sl` controller and command catalog. `pod-runtime` remains the worker bootstrap/runtime dependency.

Every remote job maintains a cached checkout at:

```text
/workspace/.sl/runtime/pod-runtime
```

and sources `helpers.sh` before loading its `.cmd` file. This gives command definitions the normal pod-runtime helper/accelerator stack without duplicating it in Podlets.

Transport configuration is inherited from `vcp` (`~/.config/vcp/config.json`). `sl` does not maintain a second SSH endpoint.

## Remote layout

```text
/workspace/.sl/
├── cache/
│   ├── commands/
│   └── seedvr2-tile/
├── runtime/
│   └── pod-runtime/
└── jobs/
    └── 20260821_120000_deadbeef/
        ├── input/
        ├── output/
        ├── work/
        ├── command.cmd
        ├── manifest.json
        ├── status.json
        ├── run.sh
        ├── pid
        └── job.log
```

Local retained metadata lives under:

```text
~/.local/state/sl/jobs/JOB/
```

## Command definitions

A command is a Bash file with directives plus `sl_run()`:

```bash
# sl:name example
# sl:description Example job
# sl:input 1
# sl:output 2
# sl:setup-version 1
# sl:memcheck

sl_prepare() {
    : # every job; cheap/idempotent
}

sl_setup() {
    : # expensive cold setup; cached by setup-version
}

sl_run() {
    some-tool "$SL_ARG_1" "$SL_ARG_2" "${SL_EXTRA_ARGS[@]}"
}
```

`sl:input N` means operand N is a local path that must be staged. `sl:output N` means operand N is a safe relative output path rooted below the remote job output directory.

Arguments after `--` remain a Bash argv array; Podlets does not `eval` an option string.

Available variables include:

```text
SL_JOB_ID
SL_JOB_DIR
SL_INPUT_DIR
SL_OUTPUT_DIR
SL_WORK_DIR
SL_CACHE_DIR
SL_COMMAND_CACHE
SL_RUNTIME_DIR
SL_ARG_1, SL_ARG_2, ...
SL_EXTRA_ARGS[]
```

## Durable execution

All jobs are launched detached from SSH. Normal `sl run` follows the remote log and fetches the output after completion; `--detach` simply returns immediately.

```bash
sl run seedvr2 input/ output/ -- --scale 2
sl run --detach seedvr2 input/ output/ -- --scale 2
```

A terminal/SSH disconnect therefore does not terminate the worker-side job.

## Memory gate

Commands opt into scheduling with:

```bash
# sl:memcheck
```

Then:

```bash
sl run --mem 18G seedvr2 ...
```

preflights physical GPU capacity before staging. If 18 GiB is possible but not currently free, the detached job waits on the worker in `WAITING_FOR_MEMORY`, polling `nvidia-smi` every five seconds and logging progress roughly every 30 seconds.

A command may eventually carry its own learned/default threshold:

```bash
# sl:memcheck 18G
```

Phase 1 does not yet measure peak VRAM; that telemetry is intentionally a follow-up after smoke testing.

## Logs, cleanup, purge

```bash
sl logs JOB
sl logs -f JOB
sl tail JOB
sl tail --no-follow JOB
```

`sl clean JOB` removes only heavy remote job data while keeping logs/metadata. `sl purge JOB` deletes the whole job record remotely and locally. A running/waiting job is protected unless `sl purge --force JOB` is used.

Default cleanup policy is `successful`: after output retrieval succeeds, heavy workspace data is removed and the cheap job record remains.

## Explicit Phase 1 non-goals

Not implemented yet:

- persistent workspaces
- `sl_init()` / resume semantics
- snapshot/hydration through HF
- pip-freeze environment reconstruction
- safe provider-side pod termination
- peak VRAM telemetry / learned memory envelopes
- `slr` provider-managed/serverless compute

Those features should layer on without changing the normal throwaway `sl run` experience.
