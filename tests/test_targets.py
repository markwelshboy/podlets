import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podlets import target_entry, targets


class TargetTests(unittest.TestCase):
    def test_set_active_target_and_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "vcp.json"
            config.write_text(
                json.dumps(
                    {
                        "hf_repo": "owner/repo",
                        "ssh": ["root@legacy"],
                        "active_target": "one",
                        "targets": {
                            "one": {"ssh": ["root@one"]},
                            "two": {"ssh": ["-p", "2222", "root@two"]},
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "VCP_CONFIG": str(config),
                "SL_TARGET_CACHE": str(root / "cache"),
            }
            targets.set_active_target("two", env)
            cfg = targets.read_vcp_config(env)
            projection = targets.create_projection("two", env)
            projected = json.loads(projection.read_text(encoding="utf-8"))

        self.assertEqual(cfg["active_target"], "two")
        self.assertNotIn("ssh", cfg)
        self.assertEqual(projected["ssh"], ["-p", "2222", "root@two"])
        self.assertEqual(projected["hf_repo"], "owner/repo")
        self.assertEqual(projected["active_target"], "two")
        self.assertEqual(set(projected["targets"]), {"two"})
        self.assertEqual(projected["targets"]["two"]["ssh"], ["-p", "2222", "root@two"])

    def test_host_first_endpoint_is_normalized_for_display(self):
        value = ["root@64.247.206.212", "-p", "14463", "-i", "/key"]
        self.assertEqual(
            targets.normalize_ssh_args(value),
            ["-p", "14463", "-i", "/key", "root@64.247.206.212"],
        )
        self.assertEqual(
            targets.endpoint_from_ssh(value),
            "root@64.247.206.212:14463",
        )

    def test_job_target_is_read_from_local_manifest(self):
        jid = "20260902_210000_deadbeef"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "jobs"
            job = state / jid
            job.mkdir(parents=True)
            (job / "manifest.json").write_text(
                json.dumps({"target": "comfydev3900"}), encoding="utf-8"
            )
            env = {"SL_STATE_DIR": str(state)}
            self.assertEqual(targets.job_target(["status", jid], env), "comfydev3900")

    def test_selected_target_prefers_job_over_active(self):
        jid = "20260902_210000_deadbeef"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "jobs"
            job = state / jid
            job.mkdir(parents=True)
            (job / "manifest.json").write_text(
                json.dumps({"target": "jobpod"}), encoding="utf-8"
            )
            vcp = root / "vcp.json"
            vcp.write_text(
                json.dumps(
                    {
                        "active_target": "otherpod",
                        "targets": {
                            "jobpod": {"ssh": ["root@job"]},
                            "otherpod": {"ssh": ["root@other"]},
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {"SL_STATE_DIR": str(state), "VCP_CONFIG": str(vcp)}
            self.assertEqual(
                targets.selected_target(["status", jid], None, env), "jobpod"
            )

    def test_explicit_target_mismatch_with_job_is_refused(self):
        jid = "20260902_210000_deadbeef"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "jobs"
            job = state / jid
            job.mkdir(parents=True)
            (job / "manifest.json").write_text(
                json.dumps({"target": "jobpod"}), encoding="utf-8"
            )
            vcp = root / "vcp.json"
            vcp.write_text(
                json.dumps(
                    {
                        "active_target": "jobpod",
                        "targets": {
                            "jobpod": {"ssh": ["root@job"]},
                            "wrongpod": {"ssh": ["root@wrong"]},
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "SL_STATE_DIR": str(state),
                "VCP_CONFIG": str(vcp),
                "SL_TARGET_CACHE": str(root / "cache"),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                rc = target_entry.entrypoint(
                    ["--target", "wrongpod", "status", jid]
                )
        self.assertEqual(rc, 1)

    def test_target_entry_projects_explicit_target_for_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vcp = root / "vcp.json"
            vcp.write_text(
                json.dumps(
                    {
                        "active_target": "one",
                        "targets": {
                            "one": {"ssh": ["root@one"]},
                            "two": {"ssh": ["-p", "2222", "root@two"]},
                        },
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "VCP_CONFIG": str(vcp),
                "SL_TARGET_CACHE": str(root / "cache"),
            }
            captured = {}

            from podlets import cli, common

            def fake_cli():
                common.VCP_CONFIG_PATH = Path(os.environ["VCP_CONFIG"])
                captured["ssh"] = common.ssh_argv()
                captured["projection"] = json.loads(
                    Path(os.environ["VCP_CONFIG"]).read_text(encoding="utf-8")
                )
                captured["target"] = os.environ.get("SL_TARGET_NAME")
                return 0

            original_config_path = common.VCP_CONFIG_PATH
            try:
                with mock.patch.dict(os.environ, env, clear=False), \
                     mock.patch.object(cli, "entrypoint", side_effect=fake_cli):
                    rc = target_entry.entrypoint(["--target", "two", "doctor"])
            finally:
                common.VCP_CONFIG_PATH = original_config_path

        self.assertEqual(rc, 0)
        self.assertEqual(captured["ssh"], ["-p", "2222", "root@two"])
        self.assertEqual(captured["target"], "two")
        self.assertEqual(captured["projection"]["active_target"], "two")
        self.assertEqual(set(captured["projection"]["targets"]), {"two"})

    def test_sl_config_ssh_delegates_discovery_to_vcp_then_bootstraps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vcp_config = root / "vcp.json"
            fake_vcp = root / "vcp"
            fake_vcp.write_text("#!/bin/sh\n", encoding="utf-8")
            env = {
                "VCP_CONFIG": str(vcp_config),
                "SL_TARGET_CACHE": str(root / "cache"),
                "SL_VCP": str(fake_vcp),
            }

            def fake_run(cmd, check=False):
                self.assertEqual(cmd[1:3], ["config", "ssh"])
                vcp_config.write_text(
                    json.dumps(
                        {
                            "version": 2,
                            "active_target": "seedvr2",
                            "targets": {
                                "seedvr2": {
                                    "ssh": ["-p", "12914", "root@213.173.109.83"],
                                    "pod_id": "pod123",
                                    "provider": "runpod",
                                }
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(cmd, 0)

            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(target_entry.subprocess, "run", side_effect=fake_run), \
                 mock.patch.object(target_entry, "_bootstrap_target") as bootstrap:
                rc = target_entry.entrypoint(
                    ["config", "ssh", "root@213.173.109.83", "-p", "12914"]
                )

        self.assertEqual(rc, 0)
        bootstrap.assert_called_once_with("seedvr2")

    def test_manifest_wrapper_records_process_target(self):
        from podlets import spec

        original = spec.manifest_for_job

        def fake_manifest(*args, **kwargs):
            return {"job_id": "test"}

        try:
            spec.manifest_for_job = fake_manifest
            target_entry._patch_manifest_target()
            with mock.patch.dict(os.environ, {"SL_TARGET_NAME": "comfydev3900"}):
                manifest = spec.manifest_for_job()
            self.assertEqual(manifest["target"], "comfydev3900")
        finally:
            spec.manifest_for_job = original

    def test_persistent_legacy_config_is_not_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "vcp.json"
            config.write_text(
                json.dumps({"ssh": ["-p", "1234", "root@legacy"]}),
                encoding="utf-8",
            )
            env = {"VCP_CONFIG": str(config)}
            self.assertIsNone(targets.active_target(targets.read_vcp_config(env)))
            self.assertIsNone(targets.selected_target(["doctor"], None, env))
            with self.assertRaises(targets.TargetError):
                targets.configure_active_ssh(["root@new"], env)


if __name__ == "__main__":
    unittest.main()
