"""Optional GUI registration never edits the real user's home configuration."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import configure_tail_mcp as registration


class ConfigureTailMCPTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.server = self.project / "Programs" / "ServerSide"
        self.python = self.server / ".venv" / "bin" / "python"
        self.script = self.server / "STTLLMTTS" / "tail_mcp_server.py"
        for path in (self.python, self.script):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# test fixture\n", encoding="utf-8")
        self.config = self.root / "settings" / "mcp.json"

    def cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with patch.object(registration, "PROJECT_ROOT", self.project), redirect_stdout(output), redirect_stderr(errors):
            code = registration.main(["--config", str(self.config), *args])
        return code, output.getvalue(), errors.getvalue()

    def write_config(self, document):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        original = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.config.write_bytes(original)
        return original

    def test_default_dry_run_prints_only_own_entry_without_creating_directories(self):
        code, output, errors = self.cli()
        self.assertEqual(code, 0, errors)
        entry = json.loads(output)["mcpServers"][registration.SERVER_NAME]
        self.assertEqual(entry, {"command": str(self.python), "args": [str(self.script)]})
        self.assertNotIn("env", entry)
        self.assertFalse(self.config.parent.exists())
        self.assertIn("Dry-run", errors)

    def test_dry_run_preserves_existing_same_name_and_never_dumps_other_secrets(self):
        original = self.write_config({"unknown": "keep-me", "mcpServers": {
            "other": {"command": "other-command", "env": {"PRIVATE_KEY": "secret-do-not-print"}},
            registration.SERVER_NAME: {"command": "old-command"},
        }})
        code, output, errors = self.cli()
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertNotIn("secret-do-not-print", output + errors)
        self.assertNotIn("other-command", output)
        self.assertEqual(list(self.config.parent.iterdir()), [self.config])

    def test_explicit_install_preserves_unknown_keys_and_is_idempotent(self):
        document = {"unknown": {"nested": [1, 2]}, "mcpServers": {
            "other": {"command": "keep", "env": {"SECRET": "unchanged"}},
            registration.SERVER_NAME: {"command": "old", "env": {"OLD_SECRET": "remove-own-env"}},
        }}
        original = self.write_config(document)
        code, _, errors = self.cli("--install")
        self.assertEqual(code, 0, errors)
        changed = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(changed["unknown"], document["unknown"])
        self.assertEqual(changed["mcpServers"]["other"], document["mcpServers"]["other"])
        self.assertEqual(changed["mcpServers"][registration.SERVER_NAME], registration.build_entry(self.project))
        backups = list(self.config.parent.glob("mcp.json.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        installed = self.config.read_bytes()
        before_stat = self.config.stat()
        code, _, errors = self.cli("--install")
        self.assertEqual(code, 0, errors)
        self.assertEqual(self.config.read_bytes(), installed)
        self.assertEqual(self.config.stat().st_mtime_ns, before_stat.st_mtime_ns)
        self.assertEqual(len(list(self.config.parent.glob("mcp.json.backup-*"))), 1)

    def test_first_install_creates_configuration_without_backup(self):
        code, _, errors = self.cli("--install")
        self.assertEqual(code, 0, errors)
        self.assertEqual(set(json.loads(self.config.read_text())["mcpServers"]), {registration.SERVER_NAME})
        self.assertEqual(list(self.config.parent.glob("mcp.json.backup-*")), [])

    def test_existing_venv_python_symlink_is_not_resolved(self):
        target = self.root / "base-python"
        target.write_text("base", encoding="utf-8")
        self.python.unlink()
        try:
            self.python.symlink_to(target)
        except OSError:
            self.skipTest("Symlinks unavailable")
        entry = registration.build_entry(self.project)
        self.assertEqual(entry["command"], str(self.python))
        self.assertNotEqual(entry["command"], str(target))

    def test_windows_python_candidate_and_explicit_override(self):
        windows = self.server / ".venv" / "Scripts" / "python.exe"
        windows.parent.mkdir()
        windows.write_text("windows", encoding="utf-8")
        with patch.object(registration.sys, "platform", "win32"):
            self.assertEqual(registration.build_entry(self.project)["command"], str(windows))
        override = self.root / "custom-python"
        override.write_text("custom", encoding="utf-8")
        code, output, errors = self.cli("--python", str(override))
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["mcpServers"][registration.SERVER_NAME]["command"], str(override))

    def test_invalid_json_and_invalid_shapes_are_never_overwritten(self):
        for content in (b"{broken", b"[]", b'{"mcpServers":[]}', b'{"x":NaN}', b'{"x":1,"x":2}'):
            with self.subTest(content=content):
                self.config.parent.mkdir(parents=True, exist_ok=True)
                self.config.write_bytes(content)
                code, output, _ = self.cli("--install")
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertEqual(self.config.read_bytes(), content)
                self.assertEqual(list(self.config.parent.glob("mcp.json.backup-*")), [])

    def test_backups_are_unique_and_replace_failure_preserves_original(self):
        self.write_config({"mcpServers": {registration.SERVER_NAME: {"command": "old"}}})
        entry = registration.build_entry(self.project)
        first = registration.configure(self.config, entry, install=True)
        changed_entry = {**entry, "args": [*entry["args"], "--different"]}
        second = registration.configure(self.config, changed_entry, install=True)
        self.assertNotEqual(first.backup_path, second.backup_path)
        original = self.config.read_bytes()
        with patch.object(registration.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaises(registration.ConfigurationError):
                registration.configure(self.config, entry, install=True)
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(list(self.config.parent.glob(".mcp.json.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
