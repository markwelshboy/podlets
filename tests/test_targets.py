import json
import os
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
        self.assertEqual(projected["ssh"], ["-p", "2222", "root@two"])
        self.assertEqual(projected["hf_repo"], "owner/repo")
        self.assertNotIn("targets", projected)
        self.assertNotIn("active_target", projected)

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
                # In the real `sl` process common.py is first imported after
                # target_entry sets VCP_CONFIG. The full test suite imports it
                # earlier, so emulate the real process ordering here.
                common.VCP_CONFIG_PATH = Path(os.environ["VCP_CONFIG"])
                captured["ssh"] = common.ssh_argv()
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

    def test_legacy_config_remains_usable_without_named_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "vcp.json"
            config.write_text(
                json.dumps({"ssh": ["-p", "1234", "root@legacy"]}),
                encoding="utf-8",
            )
            env = {"VCP_CONFIG": str(config)}
            self.assertIsNone(targets.active_target(targets.read_vcp_config(env)))
            self.assertIsNone(targets.selected_target(["doctor"], None, env))


if __name__ == "__main__":
    unittest.main()
