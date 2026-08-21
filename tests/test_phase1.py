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

    def test_build_arg_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/"images"; source.mkdir()
            cmd=Path(tmp)/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:input 1\n# sl:output 2\nsl_run() { :; }\n")
            command=spec.parse_command(cmd)
            values=spec.build_arg_values(command,[str(source),"out/results"],"/workspace/.sl","20260821_120000_deadbeef")
        self.assertTrue(values[1].endswith("/input/arg1/images")); self.assertTrue(values[2].endswith("/output/out/results"))

    def test_runner_loads_runtime_before_command_and_preserves_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd=Path(tmp)/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:memcheck\nsl_run() { :; }\n")
            command=spec.parse_command(cmd)
            script=spec.build_run_script(job_id="20260821_120000_deadbeef",spec=command,arg_values={},extra_args=["--thing","value with spaces"],remote_root="/workspace/.sl",runtime_repo="https://example.invalid/pod-runtime.git",runtime_ref="main",memory_mib=18432)
        self.assertLess(script.index('source "$SL_RUNTIME_DIR/helpers.sh"'),script.index('source "$SL_COMMAND_FILE"'))
        self.assertIn("SL_EXTRA_ARGS=(--thing 'value with spaces')",script); self.assertNotIn("eval ",script)
        self.assertLess(script.index("_sl_wait_for_memory;"),script.index('sl_run; rc=$?'))

    def test_generated_runner_is_valid_bash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); cmd=root/"demo.cmd"; cmd.write_text("# sl:name demo\n# sl:memcheck\nsl_run() { :; }\n")
            script=spec.build_run_script(job_id="20260821_120000_deadbeef",spec=spec.parse_command(cmd),arg_values={},extra_args=[],remote_root="/workspace/.sl",runtime_repo="https://example.invalid/pod-runtime.git",runtime_ref="main",memory_mib=1024)
            runner=root/"run.sh"; runner.write_text(script)
            result=subprocess.run(["bash","-n",str(runner)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_run_parser_preserves_extra_argv(self):
        ns=cli.parse_run(["--mem","18G","seedvr2","in","out","--","--config","a b.json","--seed","43"])
        self.assertEqual(ns.mem,"18G"); self.assertEqual(ns.command,"seedvr2"); self.assertEqual(ns.operands,["in","out"]); self.assertEqual(ns.extra,["--config","a b.json","--seed","43"])

    def test_alias_parser(self):
        ns=cli.parse_run(["in","out","--","--scale","2"],command_alias="seedvr2.cmd")
        self.assertEqual(ns.command,"seedvr2.cmd"); self.assertEqual(ns.extra,["--scale","2"])

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
        smoke=spec.find_command("smoke",cfg); seed=spec.find_command("seedvr2",cfg)
        self.assertTrue(smoke.memcheck); self.assertTrue(seed.memcheck); self.assertEqual(seed.inputs,[1]); self.assertEqual(seed.outputs,[2])


if __name__ == "__main__": unittest.main()
