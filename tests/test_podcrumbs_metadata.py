import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from podlets import appmeta
from podlets.common import SlError


class PodcrumbMetadataTests(unittest.TestCase):
    def make_catalog(self, root: Path) -> Path:
        commands = root / "commands"
        app = root / "apps" / "background-removal"
        commands.mkdir(parents=True)
        app.mkdir(parents=True)
        (commands / "bg-remove.cmd").write_text(
            "# sl:name bg-remove\n"
            "# sl:description Remove backgrounds\n"
            "# sl:app background-removal\n"
            "# sl:input 1\n"
            "# sl:output 2\n"
            "sl_run() { :; }\n",
            encoding="utf-8",
        )
        (app / "app.yaml").write_text(
            "schema: 1\nname: background-removal\ntitle: Background Removal Comparator\n",
            encoding="utf-8",
        )
        (app / "controls.yaml").write_text(
            "schema: 1\n"
            "controls:\n"
            "  methods:\n"
            "    flag: --methods\n"
            "    type: multi_choice\n"
            "    metavar: METHOD\n"
            "    choices: [rmbg2, birefnet_hr]\n"
            "    default: [birefnet_hr]\n"
            "    help: Methods to run.\n"
            "  recursive:\n"
            "    flag: --recursive\n"
            "    negative_flag: --no-recursive\n"
            "    type: boolean\n"
            "    default: true\n"
            "    help: Scan subdirectories.\n",
            encoding="utf-8",
        )
        (app / "config.yaml").write_text("schema: 1\nmethods:\n  rmbg2: {}\n", encoding="utf-8")
        return commands

    def test_command_help_renders_declared_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = self.make_catalog(Path(tmp))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(appmeta.command_help("bg-remove", {"command_dir": str(commands)}), 0)
            text = out.getvalue()
        self.assertIn("Background Removal Comparator", text)
        self.assertIn("Run: sl run bg-remove INPUT OUTPUT -- [controls]", text)
        self.assertIn("1: INPUT", text)
        self.assertIn("2: OUTPUT", text)
        self.assertIn("--methods METHOD...", text)
        self.assertIn("Choices: rmbg2, birefnet_hr", text)
        self.assertIn("Default: birefnet_hr", text)
        self.assertIn("--recursive / --no-recursive", text)

    def test_controls_and_structural_config_are_separate_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            commands = self.make_catalog(Path(tmp))
            controls = io.StringIO()
            config = io.StringIO()
            with contextlib.redirect_stdout(controls):
                appmeta.command_controls("bg-remove", {"command_dir": str(commands)})
            with contextlib.redirect_stdout(config):
                appmeta.command_config("bg-remove", {"command_dir": str(commands)})
        self.assertIn("flag: --methods", controls.getvalue())
        self.assertNotIn("flag: --methods", config.getvalue())
        self.assertIn("methods:", config.getvalue())

    def test_help_requires_explicit_app_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = root / "commands"
            commands.mkdir()
            (commands / "plain.cmd").write_text("# sl:name plain\nsl_run() { :; }\n", encoding="utf-8")
            with self.assertRaisesRegex(SlError, "does not declare"):
                appmeta.command_help("plain", {"command_dir": str(commands)})


if __name__ == "__main__":
    unittest.main()
