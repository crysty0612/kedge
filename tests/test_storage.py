"""
Tests for storage providers, quota safeguards, size boundary validation, and retention pruning.
"""

from pathlib import Path
import tempfile
import unittest

from kedge.storage.base import StorageProvider
from kedge.storage.local import LocalStorageProvider


class DummyProvider(StorageProvider):
    def upload(self, archive_path, tag, title, notes):
        return True, "ok"

    def download(self, tag, dest_dir):
        return True, None, "ok"

    def list_backups(self):
        return []

    def prune(self, retention_count):
        return 0


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.tmp_dir.name)
        self.provider = DummyProvider()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_size_limit_boundaries(self):
        test_file = self.test_root / "test_asset.bin"

        # 1. Normal file (10 MB) -> Should pass with OK
        test_file.write_bytes(b"\x00" * (10 * 1024 * 1024))
        ok, size_mb, msg = self.provider.check_size_limit(test_file, max_mb=1800)
        self.assertTrue(ok)
        self.assertAlmostEqual(size_mb, 10.0, places=1)
        self.assertIn("within limits", msg)

        # 2. Warning boundary (>75% of 100MB, e.g. 80MB)
        test_file.write_bytes(b"\x00" * (80 * 1024 * 1024))
        ok, size_mb, msg = self.provider.check_size_limit(test_file, max_mb=100)
        self.assertTrue(ok)
        self.assertIn("Warning", msg)

        # 3. Exceeded boundary (>100MB, e.g. 105MB) -> Should fail
        test_file.write_bytes(b"\x00" * (105 * 1024 * 1024))
        ok, size_mb, msg = self.provider.check_size_limit(test_file, max_mb=100)
        self.assertFalse(ok)
        self.assertIn("exceeds maximum boundary", msg)

    def test_local_storage_upload_download_and_pruning(self):
        storage_dir = self.test_root / "backups"
        local_store = LocalStorageProvider(str(storage_dir))

        # Upload 5 snapshots
        for i in range(5):
            f = self.test_root / f"backup_{i}.tar.gz"
            f.write_text(f"snapshot {i}")
            ok, msg = local_store.upload(f, tag=f"v{i}", title=f"Title {i}", notes="Note")
            self.assertTrue(ok)

        backups = local_store.list_backups()
        self.assertEqual(len(backups), 5)
        self.assertEqual(backups[0]["tag"], "v4")  # newest first

        # Prune with retention_count = 3
        pruned = local_store.prune(retention_count=3)
        self.assertEqual(pruned, 2)

        remaining = local_store.list_backups()
        self.assertEqual(len(remaining), 3)
        self.assertEqual(remaining[0]["tag"], "v4")
        self.assertEqual(remaining[1]["tag"], "v3")
        self.assertEqual(remaining[2]["tag"], "v2")

        # Download latest
        dl_dir = self.test_root / "downloaded"
        ok, dl_file, msg = local_store.download(tag=None, dest_dir=dl_dir)
        self.assertTrue(ok)
        self.assertIsNotNone(dl_file)
        self.assertEqual(dl_file.read_text(), "snapshot 4")


if __name__ == "__main__":
    unittest.main()
