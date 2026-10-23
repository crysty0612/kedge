"""
Comprehensive End-to-End lifecycle test:
Simulated workspace -> Discovery -> Atomic hot backup -> Encryption -> Decryption -> Restore in sandbox -> Database parity validation.
"""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kedge.config import ConfigManager
from kedge.engine.backup_engine import BackupEngine
from kedge.engine.restore_engine import RestoreEngine
from kedge.engine.sqlite_engine import SQLiteEngine


class TestEndToEndLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name)

        # 1. Workspace directories
        self.workspace = self.root / "homelab"
        self.containers_dir = self.workspace / "containers"
        self.data_dir = self.workspace / "data"
        self.backup_storage_dir = self.root / "remote_storage"
        self.restore_sandbox = self.root / "sandbox_restore"

        self.containers_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.backup_storage_dir.mkdir(parents=True, exist_ok=True)
        self.restore_sandbox.mkdir(parents=True, exist_ok=True)

        # 2. Mock stack (vaultwarden)
        vw_stack = self.containers_dir / "vaultwarden"
        vw_stack.mkdir(parents=True, exist_ok=True)
        (vw_stack / "compose.yaml").write_text("services:\n  vaultwarden:\n    image: vaultwarden/server\n")
        (vw_stack / ".env").write_text("ADMIN_TOKEN=secret_token_12345\nSIGNUPS_ALLOWED=false\n")

        # 3. Mock data with active SQLite database in WAL mode
        vw_data = self.data_dir / "vaultwarden"
        vw_data.mkdir(parents=True, exist_ok=True)
        self.db_path = vw_data / "db.sqlite3"

        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE ciphers (id TEXT PRIMARY KEY, name TEXT, notes TEXT);")
        for i in range(50):
            conn.execute("INSERT INTO ciphers VALUES (?, ?, ?);", (f"id_{i}", f"Login {i}", f"Note for {i}"))
        conn.commit()
        conn.close()

        # 4. Mock host config
        self.mock_sysctl = self.root / "99-dns-buffers.conf"
        self.mock_sysctl.write_text("net.core.rmem_max = 8388608\n")

        # 5. Kedge Configuration
        self.config_file = self.workspace / ".kedge.yaml"
        self.cfg_mgr = ConfigManager(explicit_path=str(self.config_file))
        self.cfg_mgr.data = {
            "version": 1,
            "workspace_root": str(self.workspace),
            "containers_dir": "containers",
            "data_paths": [str(self.data_dir)],
            "tracked_stacks": ["vaultwarden"],
            "tracked_host_items": ["sysctl"],
            "custom_host_paths": [str(self.mock_sysctl)],
            "encryption": {
                "enabled": True,
                "algorithm": "AES256",
                "key_file": str(self.root / "kedge.key"),
            },
            "storage": {
                "backend": "local",
                "local_path": str(self.backup_storage_dir),
                "retention_count": 5,
                "max_asset_size_mb": 1800,
            },
            "gitops": {"enabled": False},
            "scheduler": {"enabled": False},
        }
        self.cfg_mgr.save()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_full_backup_and_restore_cycle(self):
        # 1. Run Backup
        backup_engine = BackupEngine(self.cfg_mgr)
        ok, msg = backup_engine.run(dry_run=False)
        self.assertTrue(ok, f"Backup failed: {msg}")

        # Check backup file created in storage
        storage_files = list(self.backup_storage_dir.glob("*.gpg"))
        self.assertEqual(len(storage_files), 1)
        self.assertTrue(storage_files[0].is_file())
        self.assertGreater(storage_files[0].stat().st_size, 0)

        # 2. Run Restore into Sandbox
        restore_engine = RestoreEngine(self.cfg_mgr)
        ok, msg = restore_engine.run(target_dir=str(self.restore_sandbox), dry_run=False)
        self.assertTrue(ok, f"Restore failed: {msg}")

        # 3. Validate restored files
        restored_db = self.restore_sandbox / "data" / "vaultwarden" / "db.sqlite3"
        if not restored_db.is_file():
            # Check if extracted under relative path
            restored_db = self.restore_sandbox / "vaultwarden" / "db.sqlite3"

        self.assertTrue(restored_db.is_file(), f"Restored DB missing at {restored_db}")

        # Run SQLite integrity check on restored database
        ok, check_msg = SQLiteEngine.check_integrity(restored_db)
        self.assertTrue(ok, f"Restored database failed integrity check: {check_msg}")

        # Validate database records
        conn = sqlite3.connect(str(restored_db))
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM ciphers;")
        count = cursor.fetchone()[0]
        conn.close()
        self.assertEqual(count, 50)

        # Check restored secret
        restored_env = self.restore_sandbox / "manifest" / "secrets" / "vaultwarden" / ".env"
        self.assertTrue(restored_env.is_file())
        self.assertIn("ADMIN_TOKEN=secret_token_12345", restored_env.read_text())

        # Check host reconstitution script
        restore_script = self.restore_sandbox / "manifest" / "host" / "restore-host.sh"
        self.assertTrue(restore_script.is_file())

    def test_bulk_media_detection(self):
        from kedge.discovery.docker_sniff import DockerSniffer
        # Root share / partition
        self.assertTrue(DockerSniffer.is_bulk_media_mount("/mnt", "/mnt"))
        self.assertTrue(DockerSniffer.is_bulk_media_mount("/media", "/media"))
        self.assertTrue(DockerSniffer.is_bulk_media_mount("/storage", "/storage"))

        # Bulk media keywords
        self.assertTrue(DockerSniffer.is_bulk_media_mount("/mnt/storage/movies", "/movies"))
        self.assertTrue(DockerSniffer.is_bulk_media_mount("/srv/downloads", "/downloads"))
        self.assertTrue(DockerSniffer.is_bulk_media_mount("/mnt/data/photos", "/library/photos"))

        # Normal container state paths (should NOT be flagged as bulk media)
        self.assertFalse(DockerSniffer.is_bulk_media_mount("/opt/stacks/app/data", "/data"))
        self.assertFalse(DockerSniffer.is_bulk_media_mount("/var/lib/app/config", "/app/config"))
        self.assertFalse(DockerSniffer.is_bulk_media_mount("/srv/docker/vault/db", "/config"))

    def test_multi_root_external_volumes(self):
        # Create external disk mount path outside workspace
        external_disk = self.root / "external_disk" / "app_state"
        external_disk.mkdir(parents=True, exist_ok=True)
        ext_db = external_disk / "app.db"

        conn = sqlite3.connect(str(ext_db))
        conn.execute("CREATE TABLE kv (k TEXT, v TEXT);")
        conn.execute("INSERT INTO kv VALUES ('key1', 'val1');")
        conn.commit()
        conn.close()

        # Add external disk to data_paths
        self.cfg_mgr.data["data_paths"].append(str(external_disk))
        self.cfg_mgr.save()

        backup_engine = BackupEngine(self.cfg_mgr)
        ok, msg = backup_engine.run(dry_run=False)
        self.assertTrue(ok)

        # Restore into new sandbox
        restore_sandbox2 = self.root / "sandbox2"
        restore_sandbox2.mkdir(parents=True, exist_ok=True)
        restore_engine = RestoreEngine(self.cfg_mgr)
        ok, msg = restore_engine.run(target_dir=str(restore_sandbox2), dry_run=False)
        self.assertTrue(ok)

        # External disk should be preserved under volumes/ hierarchy without collisions
        expected_rel = "volumes/" + str(external_disk).lstrip("/") + "/app.db"
        restored_ext_db = restore_sandbox2 / expected_rel
        self.assertTrue(restored_ext_db.is_file(), f"Missing restored external DB at {restored_ext_db}")

        ok, check_msg = SQLiteEngine.check_integrity(restored_ext_db)
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
