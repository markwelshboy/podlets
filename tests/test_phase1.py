import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podlets import cli, common, memory, spec


class Phase1Tests(unittest.TestCase):
    def test_parse_memory(self):
        self.assertEqual(memory.parse_memory_mib("18G"), 18432)
        self.assertEqual(memory.parse_memory_mib("18000MiB"), 18000)
        self.assertEqual(memory.parse_memory_mib("1.5GB"), 1536)
        with self.assertRaises(common.SlError): memory.parse_memory_mib("18")

    def test_parse_command_typed_args_and_memcheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"demo.cmd"
            path.write_text("# sl:name demo\n# sl:input 1\n# sl:output 2\n# sl:setup-version 3\n# sl:memcheck 18G\nsl_run() { :; }\n")
            command=spec.parse_command(path)
        self.assertEqual(command.inputs,[1]); self.assertEqual(command.outputs,[2]); self.assertEqual(command.setup_version,"3")
        self.assertTrue(command.memcheck); self.assertEqual(command.memcheck_default,"18G")

    def test_output_path_rejects_escape(self):
        for value in ("../oops","/tmp/oops","foo/../bar",""):
            with self.subTest(value=value):
                with self.assertRaises(common.SlError): spec.validate_output_arg(value)
        self.assertEqual(spec.validate_output_arg("results/run1/"),"results/run1")

    def test_output_directory_must_exist_and_be_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            self.assertEqual(common.validate_output_dir(root),root.resolve())
            with self.assertRaises(common.SlError): common.validate_output_dir(root/"missing")
            file_path=root/"not-a-dir"; file_path.write_text("x")
            with self.assertRaises(common.SlError): common.validate_output_dir(file_path)

    def test_build_arg_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"images"; source.mkdir()
            cmd=Path(tmp)/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:input 1\n# sl:output 2\nsl_run() { :; }\n")
            command=spec.parse_command(cmd)
            values=spec.build_arg_values(command,[str(source),"out/results"],"/workspace/.sl","20260821_120000_deadbeef")
        self.assertTrue(values[1].endswith("/input/arg1/images")); self.assertTrue(values[2].endswith("/output/out/results"))

    def test_glob_input_stages_stable_root_and_preserves_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"input"; source.mkdir(); (source/"one.png").write_text("x")
            cmd=root/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:input 1\n# sl:output 2\nsl_run() { :; }\n")
            command=spec.parse_command(cmd)
            old=os.getcwd()
            try:
                os.chdir(root)
                plan=spec.plan_input("input/*.png","/workspace/.sl/jobs/J/input/arg1")
                values=spec.build_arg_values(command,["input/*.png","out"],"/workspace/.sl","20260821_120000_deadbeef")
            finally:
                os.chdir(old)
        self.assertEqual(plan.stage_source,source.resolve())
        self.assertTrue(plan.is_glob)
        self.assertEqual(plan.remote_value,"/workspace/.sl/jobs/J/input/arg1/input/*.png")
        self.assertTrue(values[1].endswith("/input/arg1/input/*.png"))

    def test_glob_input_manifest_records_staging_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/"input"; source.mkdir()
            cmd=root/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:input 1\n# sl:output 2\nsl_run() { :; }\n")
            command=spec.parse_command(cmd)
            old=os.getcwd()
            try:
                os.chdir(root)
                values=spec.build_arg_values(command,["input/*.png","out"],"/workspace/.sl","20260821_120000_deadbeef")
                manifest=spec.manifest_for_job(job_id="20260821_120000_deadbeef",spec=command,operands=["input/*.png","out"],extra_args=[],output_dir=root,remote_root="/workspace/.sl",arg_values=values)
            finally:
                os.chdir(old)
        entry=manifest["inputs"][0]
        self.assertEqual(entry["local"],"input/*.png")
        self.assertEqual(entry["staged_from"],str(source.resolve()))
        self.assertTrue(entry["glob"])

    def test_runner_loads_runtime_before_command_and_preserves_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd=Path(tmp)/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:memcheck\nsl_run() { :; }\n")
            command=spec.parse_command(cmd)
            script=spec.build_run_script(job_id="20260821_120000_deadbeef",spec=command,arg_values={},extra_args=["--thing","value with spaces"],remote_root="/workspace/.sl",runtime_repo="https://example.invalid/pod-runtime.git",runtime_ref="main",memory_mib=18432,verbosity_mode="debug")
        self.assertLess(script.index('source "$SL_RUNTIME_DIR/helpers.sh"'),script.index('source "$SL_COMMAND_FILE"'))
        self.assertIn("SL_EXTRA_ARGS=(--thing 'value with spaces')",script); self.assertNotIn("eval ",script)
        self.assertLess(script.index("_sl_wait_for_memory;"),script.index('_sl_phase RUN sl_run; rc=$?'))
        self.assertIn("git clone --quiet --depth 1 --no-tags",script)
        self.assertIn("fetch --quiet --depth 1 --no-tags origin main",script)
        self.assertIn("export SL_VERBOSITY=debug",script)
        self.assertIn('export SL_STATUS_FILE="$SL_JOB_DIR/status.json"',script)
        self.assertIn('SL_CONTROL_PYTHON="$(command -v python3 || true)"',script)
        self.assertIn('"$SL_CONTROL_PYTHON" - "$SL_STATUS_FILE"',script)
        self.assertIn("export SL_GPU_TELEMETRY_ENABLED=1",script)
        self.assertIn("sleep 0.5",script)
        self.assertNotIn("--loop-ms=500",script)
        self.assertIn('sample="$(nvidia-smi -i 0 --query-gpu=memory.used',script)
        self.assertIn("printf '%s\\n' \"$sample\" >> \"$SL_GPU_SAMPLES_FILE\"",script)
        self.assertNotIn("tr -d '[:space:]' >> \"$SL_GPU_SAMPLES_FILE\"",script)
        self.assertIn("GPU telemetry unavailable:",script)
        self.assertIn("_sl_gpu_monitor_start",script)
        self.assertIn("_sl_gpu_monitor_stop_and_report",script)
        self.assertIn("SUGGESTED --mem",script)
        self.assertIn('"gpu_telemetry": telemetry',script)
        self.assertIn("_sl_phase PREPARE sl_prepare",script)
        self.assertIn("_sl_phase SETUP sl_setup",script)
        self.assertIn("_sl_phase RUN sl_run",script)

    def test_non_memcheck_command_disables_gpu_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd=Path(tmp)/"demo.cmd"; cmd.write_text("# sl:name demo\nsl_run() { :; }\n")
            script=spec.build_run_script(job_id="20260821_120000_deadbeef",spec=spec.parse_command(cmd),arg_values={},extra_args=[],remote_root="/workspace/.sl",runtime_repo="https://example.invalid/pod-runtime.git",runtime_ref="main")
        self.assertIn("export SL_GPU_TELEMETRY_ENABLED=0",script)

    def test_generated_runner_is_valid_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cmd=root/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:memcheck\nsl_run() { :; }\n")
            script=spec.build_run_script(job_id="20260821_120000_deadbeef",spec=spec.parse_command(cmd),arg_values={},extra_args=[],remote_root="/workspace/.sl",runtime_repo="https://example.invalid/pod-runtime.git",runtime_ref="main",memory_mib=1024,verbosity_mode="run")
            runner=root/"run.sh"; runner.write_text(script)
            result=subprocess.run(["bash","-n",str(runner)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        for marker in ("PY_STATUS", "PY_GPU", "PY_REPORT"):
            opener = f"<<'{marker}'\n"
            start = script.index(opener) + len(opener)
            end = script.index(f"\n{marker}", start)
            compile(script[start:end], f"generated-{marker}.py", "exec")

    def test_run_parser_preserves_extra_argv(self):
        ns=cli.parse_run(["--mem","18G","--verbosity","debug","seedvr2","in","out","--","--config","a b.json","--seed","43"])
        self.assertEqual(ns.mem,"18G"); self.assertEqual(ns.verbosity,"debug"); self.assertEqual(ns.command,"seedvr2"); self.assertEqual(ns.operands,["in","out"]); self.assertEqual(ns.extra,["--config","a b.json","--seed","43"])

    def test_verbosity_defaults_and_validation(self):
        self.assertEqual(common.verbosity({}),"run")
        self.assertEqual(common.verbosity({},"none"),"none")
        self.assertEqual(common.verbosity({},"full"),"full")
        with self.assertRaises(common.SlError): common.verbosity({},"chatty")

    def test_alias_parser(self):
        ns=cli.parse_run(["in","out","--","--scale","2"],command_alias="seedvr2.cmd")
        self.assertEqual(ns.command,"seedvr2.cmd"); self.assertEqual(ns.extra,["--scale","2"])

    def test_cancel_command_terminates_process_group_and_retains_workspace(self):
        jid="20260824_180000_deadbeef"
        result=type("Result",(),{"returncode":0,"stdout":"","stderr":""})()
        with mock.patch.object(cli,"sl_config",return_value={}), \
             mock.patch.object(cli,"remote_status",return_value={"state":"RUNNING"}), \
             mock.patch.object(cli,"ssh",return_value=result) as ssh_mock, \
             mock.patch.object(cli,"sync_metadata") as sync_mock:
            self.assertEqual(cli.cancel_command([jid]),0)
        script=ssh_mock.call_args.args[0]
        self.assertIn('kill -TERM -- "-$pid"',script)
        self.assertIn('kill -KILL -- "-$pid"',script)
        self.assertIn('data["state"]="CANCELLED"',script)
        self.assertIn('job cancelled by controller',script)
        self.assertNotIn("rm -rf",script)
        sync_mock.assert_called_once_with(jid,{})

    def test_vcp_ssh_config_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"vcp.json"; path.write_text('{"ssh":["-p","1234","root@host"]}')
            with mock.patch.object(common,"VCP_CONFIG_PATH",path): self.assertEqual(common.ssh_argv(),["-p","1234","root@host"])

    def test_vcp_path_can_use_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate=Path(tmp)/"vcp"; candidate.write_text("#!/bin/sh\n")
            self.assertEqual(common.vcp_path({"vcp":str(candidate)}),candidate.resolve())

    def test_builtin_commands_parse(self):
        cfg={"command_dir":str(Path(__file__).resolve().parents[1]/"commands")}
        smoke=spec.find_command("smoke",cfg)
        seed=spec.find_command("seedvr2",cfg)
        sweep=spec.find_command("seedvr2-sweep",cfg)
        self.assertTrue(smoke.memcheck)
        self.assertTrue(seed.memcheck); self.assertEqual(seed.inputs,[1]); self.assertEqual(seed.outputs,[2])
        self.assertTrue(sweep.memcheck); self.assertEqual(sweep.inputs,[1]); self.assertEqual(sweep.outputs,[2])


if __name__ == "__main__": unittest.main()