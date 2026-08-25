import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podlets import bootstrap, cli, common


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

    def test_run_auto_bootstraps_before_dispatch(self):
        calls = []
        with mock.patch.object(cli, 'sl_config', return_value={}), \
             mock.patch.object(cli, 'ensure_worker_runtime', side_effect=lambda cfg: calls.append('bootstrap')), \
             mock.patch.object(cli, 'run_job', side_effect=lambda args: calls.append('run') or 0):
            self.assertEqual(cli.main(['run', 'smoke']), 0)
        self.assertEqual(calls, ['bootstrap', 'run'])


if __name__ == '__main__':
    unittest.main()
