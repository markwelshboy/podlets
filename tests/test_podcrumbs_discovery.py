import tempfile
import unittest
from pathlib import Path
from unittest import mock

from podlets import common, spec


class PodcrumbsDiscoveryTests(unittest.TestCase):
    def test_default_podcrumbs_checkout_is_searched_after_builtins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builtin = root / "podlets" / "commands"
            crumbs = root / "podcrumbs" / "commands"
            builtin.mkdir(parents=True)
            crumbs.mkdir(parents=True)
            (crumbs / "bg-remove.cmd").write_text(
                "# sl:name bg-remove\nsl_run() { :; }\n",
                encoding="utf-8",
            )
            with mock.patch.object(common, "DEFAULT_COMMAND_DIR", builtin), \
                 mock.patch.object(common, "DEFAULT_PODCRUMBS_COMMAND_DIRS", [crumbs]):
                dirs = common.command_dirs({})
                command = spec.find_command("bg-remove", {})
        self.assertEqual(dirs, [builtin, crumbs])
        self.assertEqual(command.name, "bg-remove")
        self.assertEqual(command.path, crumbs / "bg-remove.cmd")

    def test_explicit_command_dir_keeps_highest_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit = root / "custom"
            builtin = root / "builtin"
            crumbs = root / "podcrumbs" / "commands"
            for path in (explicit, builtin, crumbs):
                path.mkdir(parents=True)
            with mock.patch.object(common, "DEFAULT_COMMAND_DIR", builtin), \
                 mock.patch.object(common, "DEFAULT_PODCRUMBS_COMMAND_DIRS", [crumbs]):
                dirs = common.command_dirs({"command_dir": str(explicit)})
        self.assertEqual(dirs, [explicit, builtin, crumbs])


if __name__ == "__main__":
    unittest.main()
