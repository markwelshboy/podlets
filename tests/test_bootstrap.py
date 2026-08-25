import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podlets import bootstrap, cli, common, jobs


class BootstrapTests(unittest.TestCase):
    def test_bootstrap_script_uses_private_runtime_and_installs_minimal_prereqs(self):
        script = bootstrap.build_bootstrap_script({})
        self.assertIn('/workspace/.sl/runtime/pod-runtime', script)
        self.assertIn('/workspace/pod-runtime', script)
        self.assertNotIn('/workspace/pod_runtime', script)
        self.assertIn('python3 -m venv', script)
        self.assertIn('apt-get install -y', script)
        self.assertIn('helpers_shell.sh', script)
        self.assertLess(script.index('packages=()'), script.index('if [[ -n "$existing" ]]'))

    def test_ensure_worker_runtime_accepts_bootstrapped_response(self):
        result = type('Result', (), {
            'returncode': 0,
            'stdout': 'bootstrapped\t/workspace/.sl/runtime/pod-runtime\n',
            'stderr': '',
        })()
        with mock.patch.object(bootstrap, 'ssh', return_value=result):
            path = bootstrap.ensure_worker_runtime({})
        self.assertEqual(path, '/workspace/.sl/runtime/pod-runtime')

    def test_config_ssh_writes_shared_vcp_config_and_bootstraps(self):
        with tempfile.TemporaryDirectory() as tmp:
            vcp_config = Path(tmp) / 'vcp.json'
            with mock.patch.object(cli, 'VCP_CONFIG_PATH', vcp_config), \
                 mock.patch.object(common, 'VCP_CONFIG_PATH', vcp_config), \
                 mock.patch.object(cli, 'sl_config', return_value={}), \
                 mock.patch.object(cli, 'vcp_path', return_value=Path('/tmp/vcp')), \
                 mock.patch.object(cli, 'ensure_worker_runtime') as ensure:
                self.assertEqual(cli.config_command(['ssh', '-p', '1234', 'root@host']), 0)
            data = json.loads(vcp_config.read_text(encoding='utf-8'))
        self.assertEqual(data['ssh'], ['-p', '1234', 'root@host'])
        ensure.assert_called_once_with({}, announce=True)

    def test_cli_dispatches_run_without_prebootstrap(self):
        with mock.patch.object(cli, 'sl_config', return_value={}), \
             mock.patch.object(cli, 'ensure_worker_runtime') as ensure, \
             mock.patch.object(cli, 'run_job', return_value=0) as run:
            self.assertEqual(cli.main(['run', 'smoke']), 0)
        ensure.assert_not_called()
        run.assert_called_once()

    def test_invalid_command_does_not_bootstrap_worker(self):
        args = cli.parse_run(['definitely-not-a-command'])
        with mock.patch.object(jobs, 'ensure_worker_runtime') as ensure:
            with self.assertRaises(common.SlError):
                jobs.run_job(args)
        ensure.assert_not_called()

    def test_missing_controller_token_fails_before_bootstrap(self):
        args = cli.parse_run(['smoke'])
        with mock.patch.dict(os.environ, {'HF_TOKEN': ''}), \
             mock.patch.object(jobs, 'ensure_worker_runtime') as ensure:
            with self.assertRaisesRegex(common.SlError, 'HF_TOKEN is not set on the controller'):
                jobs.run_job(args)
        ensure.assert_not_called()


if __name__ == '__main__':
    unittest.main()
