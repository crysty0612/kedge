"""
Tests for Kedge CLI command dispatcher and flag handling.
"""

from pathlib import Path
import tempfile
import unittest

from kedge.cli import build_parser, cmd_add, cmd_drop, cmd_init, cmd_status, cmd_verify
from kedge.config import ConfigManager


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)
        self.config_path = self.root / ".kedge.yaml"
        self.cfg_mgr = ConfigManager(explicit_path=str(self.config_path))
        self.cfg_mgr.data["workspace_root"] = str(self.root)
        self.cfg_mgr.data["encryption"]["key_file"] = str(self.root / "kedge.key")
        self.cfg_mgr.save()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_parser_commands(self):
        parser = build_parser()
        args = parser.parse_args(["discover", "--json"])
        self.assertEqual(args.command, "discover")
        self.assertTrue(args.json)

        args_scan = parser.parse_args(["scan"])
        self.assertEqual(args_scan.command, "scan")

        args_cfg = parser.parse_args(["config", "set", "retention", "14"])
        self.assertEqual(args_cfg.command, "config")
        self.assertEqual(args_cfg.action, "set")
        self.assertEqual(args_cfg.key, "retention")
        self.assertEqual(args_cfg.value, "14")

        args_b = parser.parse_args(["backup", "--dry-run"])
        self.assertEqual(args_b.command, "backup")
        self.assertTrue(args_b.dry_run)

    def test_cli_add_and_drop(self):
        test_path = str(self.root / "my_data")

        # Add data
        cmd_add(self.cfg_mgr, target=test_path, target_type="data")
        self.assertIn(test_path, self.cfg_mgr.data["data_paths"])

        # Add stack
        cmd_add(self.cfg_mgr, target="traefik", target_type="stack")
        self.assertIn("traefik", self.cfg_mgr.data["tracked_stacks"])

        # Drop data
        cmd_drop(self.cfg_mgr, target=test_path)
        self.assertNotIn(test_path, self.cfg_mgr.data["data_paths"])

        # Drop stack
        cmd_drop(self.cfg_mgr, target="traefik")
        self.assertNotIn("traefik", self.cfg_mgr.data["tracked_stacks"])

    def test_cli_verify_and_status(self):
        # Generate key so verify succeeds
        from kedge.crypto.gpg import CryptoEngine
        CryptoEngine.ensure_key_file(self.cfg_mgr.data["encryption"]["key_file"])

        code = cmd_verify(self.cfg_mgr)
        self.assertEqual(code, 0)

        status_code = cmd_status(self.cfg_mgr)
        self.assertEqual(status_code, 0)

    def test_elevation_check(self):
        from kedge.cli import check_and_elevate_privileges
        from unittest.mock import patch
        # If root, should return immediately without error
        with patch("os.geteuid", return_value=0):
            check_and_elevate_privileges("status")
        # If KEDGE_NO_ELEVATE=1, should return immediately
        with patch.dict("os.environ", {"KEDGE_NO_ELEVATE": "1"}), patch("os.geteuid", return_value=1000):
            check_and_elevate_privileges("init")


if __name__ == "__main__":
    unittest.main()
