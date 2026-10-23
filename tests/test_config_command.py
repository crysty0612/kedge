"""
Tests for kedge config command and aliases.
"""

from pathlib import Path
import tempfile
import unittest

from kedge.cli import cmd_config
from kedge.config import ConfigManager


class TestConfigCommand(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.config_path = self.root / ".kedge.yaml"
        self.cfg_mgr = ConfigManager(explicit_path=str(self.config_path))

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_config_set_and_get(self):
        # Set remote URL
        code = cmd_config(self.cfg_mgr, "set", "remote", "git@github.com:myuser/homelab-orchestrator.git")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg_mgr.get_value("remote"), "git@github.com:myuser/homelab-orchestrator.git")
        self.assertEqual(self.cfg_mgr.get_value("storage.github_repo"), "myuser/homelab-orchestrator")

        # Set retention
        code = cmd_config(self.cfg_mgr, "set", "retention", "21")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg_mgr.get_value("retention"), 21)

        # Set custom key
        code = cmd_config(self.cfg_mgr, "set", "gitops.periphery_port", "9000")
        self.assertEqual(code, 0)
        self.assertEqual(self.cfg_mgr.get_value("gitops.periphery_port"), 9000)

        # Show table
        code_show = cmd_config(self.cfg_mgr, "show")
        self.assertEqual(code_show, 0)

        # Reset
        code_reset = cmd_config(self.cfg_mgr, "reset")
        self.assertEqual(code_reset, 0)
        self.assertEqual(self.cfg_mgr.get_value("retention"), 14)

    def test_root_cwd_safety(self):
        import os
        from unittest.mock import patch
        with patch.object(Path, "cwd", return_value=Path("/")), patch("os.geteuid", return_value=1000):
            mgr = ConfigManager()
            self.assertNotEqual(mgr.config_path, Path("/.kedge.yaml"))
            self.assertFalse(str(mgr.config_path).startswith("/.kedge"))

    def test_set_config_location(self):
        new_loc = self.root / "subfolder" / ".kedge.yaml"
        self.cfg_mgr.set_config_location(new_loc)
        self.assertEqual(self.cfg_mgr.config_path, new_loc)
        saved_path = self.cfg_mgr.save()
        self.assertTrue(saved_path.is_file())
        self.assertEqual(saved_path, new_loc)


if __name__ == "__main__":
    unittest.main()
