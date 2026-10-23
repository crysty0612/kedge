"""
Tests for online SQLite backup engine and magic-byte detection under active WAL mode.
"""

from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from kedge.core.models import DBTarget
from kedge.discovery.sqlite_sniff import SQLiteSniffer
from kedge.engine.sqlite_engine import SQLiteEngine


class TestSQLiteEngine(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.tmp_dir.name)
        self.db_path = self.test_root / "test_service.sqlite3"

        # Create active SQLite database in WAL mode
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, email TEXT);")
        for i in range(100):
            conn.execute("INSERT INTO users (name, email) VALUES (?, ?);", (f"User {i}", f"user{i}@example.com"))
        conn.commit()
        conn.close()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_magic_byte_detection(self):
        # 1. Real database
        self.assertTrue(SQLiteSniffer.is_sqlite_file(self.db_path))

        # 2. Fake database with .db extension but plain text
        fake_file = self.test_root / "fake.db"
        fake_file.write_text("Hello World this is not sqlite")
        self.assertFalse(SQLiteSniffer.is_sqlite_file(fake_file))

        # 3. Extensionless database with valid header
        extensionless = self.test_root / "my_secret_store"
        with open(extensionless, "wb") as f:
            f.write(b"SQLite format 3\x00" + b"\x00" * 100)
        self.assertTrue(SQLiteSniffer.is_sqlite_file(extensionless))

    def test_online_backup_atomic_consistency(self):
        target = DBTarget(
            path=str(self.db_path),
            size_bytes=self.db_path.stat().st_size,
            relative_path="test_service.sqlite3",
            format="sqlite3",
            has_wal=True,
        )

        stage_dir = self.test_root / "backup_stage"
        ok, dest_file, msg = SQLiteEngine.backup_database(target, stage_dir, verify_integrity=True)

        self.assertTrue(ok, f"Backup failed: {msg}")
        self.assertIsNotNone(dest_file)
        self.assertTrue(dest_file.is_file())

        # Verify record count in backup
        conn = sqlite3.connect(str(dest_file))
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM users;")
        count = cursor.fetchone()[0]
        conn.close()

        self.assertEqual(count, 100)

    def test_backup_under_active_concurrent_wal_writes(self):
        """Simulates Vaultwarden or Cosmos actively writing to SQLite while Kedge snapshots."""
        stop_writing = threading.Event()
        write_count = [0]

        def active_writer():
            w_conn = sqlite3.connect(str(self.db_path))
            while not stop_writing.is_set():
                w_conn.execute("INSERT INTO users (name, email) VALUES (?, ?);", ("Live", "live@example.com"))
                w_conn.commit()
                write_count[0] += 1
                time.sleep(0.005)
            w_conn.close()

        writer_thread = threading.Thread(target=active_writer, daemon=True)
        writer_thread.start()

        # Let some writes happen
        time.sleep(0.05)

        target = DBTarget(
            path=str(self.db_path),
            size_bytes=self.db_path.stat().st_size,
            relative_path="live_service.sqlite3",
            format="sqlite3",
            has_wal=True,
        )

        stage_dir = self.test_root / "concurrent_stage"
        ok, dest_file, msg = SQLiteEngine.backup_database(target, stage_dir, verify_integrity=True)

        stop_writing.set()
        writer_thread.join(timeout=2.0)

        self.assertTrue(ok, f"Concurrent backup failed: {msg}")
        self.assertIsNotNone(dest_file)

        # Integrity check on snapshot
        ok, integrity_msg = SQLiteEngine.check_integrity(dest_file)
        self.assertTrue(ok, f"Integrity check failed: {integrity_msg}")

        # Check records
        conn = sqlite3.connect(str(dest_file))
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM users;")
        total_in_backup = cursor.fetchone()[0]
        conn.close()

        # The backup must contain at least the initial 100 rows
        self.assertGreaterEqual(total_in_backup, 100)


if __name__ == "__main__":
    unittest.main()
