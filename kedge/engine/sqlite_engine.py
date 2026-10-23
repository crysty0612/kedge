"""
Online SQLite backup engine.
Executes non-blocking atomic backups via Python's native SQLite Backup API or CLI.
Performs PRAGMA integrity checks.
"""

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Optional, Tuple

from kedge.core.models import DBTarget
from kedge.core.runner import Runner


class SQLiteEngine:
    @staticmethod
    def check_integrity(db_path: Path) -> Tuple[bool, str]:
        if not db_path.is_file():
            return False, f"File not found: {db_path}"

        try:
            # Open URI with immutable or read-only mode to prevent lock contention
            uri = f"file:{db_path.resolve()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=10.0)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            rows = cursor.fetchall()
            conn.close()

            if rows and rows[0][0] == "ok":
                return True, "ok"
            errs = [str(r[0]) for r in rows]
            return False, "; ".join(errs)
        except Exception as e:
            # Fallback to CLI sqlite3 if Python sqlite3 has driver differences
            if Runner.has_command("sqlite3"):
                res = Runner.run(["sqlite3", str(db_path), "PRAGMA integrity_check;"], timeout=10)
                if res.ok and res.stdout.strip() == "ok":
                    return True, "ok"
                return False, res.stderr or res.stdout or str(e)
            return False, str(e)

    @classmethod
    def backup_database(
        cls,
        target: DBTarget,
        dest_dir: Path,
        verify_integrity: bool = True,
    ) -> Tuple[bool, Optional[Path], str]:
        src_path = Path(target.path)
        if not src_path.is_file():
            return False, None, f"Source database not found: {src_path}"

        dest_file = dest_dir / target.relative_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if verify_integrity:
            ok, msg = cls.check_integrity(src_path)
            if not ok:
                return False, None, f"Source database integrity failed: {msg}"

        # Perform online hot backup using Python's native sqlite3 API
        try:
            # Using read-only source URI allows copying even while WAL transactions are in flight
            src_uri = f"file:{src_path.resolve()}?mode=ro"
            src_conn = sqlite3.connect(src_uri, uri=True, timeout=15.0)
            dest_conn = sqlite3.connect(str(dest_file))

            with dest_conn:
                src_conn.backup(dest_conn, pages=100, sleep=0.01)

            dest_conn.close()
            src_conn.close()
        except Exception as e:
            # Fallback to sqlite3 CLI online backup command
            if Runner.has_command("sqlite3"):
                cmd = f"sqlite3 \"{src_path}\" \".backup '{dest_file}'\""
                res = Runner.run(cmd, shell=True, timeout=30)
                if not res.ok:
                    return False, None, f"CLI backup failed: {res.stderr}"
            else:
                return False, None, f"Native SQLite backup failed: {e}"

        # Preserve permissions
        try:
            shutil.copystat(src_path, dest_file)
        except OSError:
            pass

        # Verify integrity of the destination backup
        if verify_integrity:
            ok, msg = cls.check_integrity(dest_file)
            if not ok:
                return False, None, f"Restored backup integrity check failed: {msg}"

        return True, dest_file, "Backup completed successfully"
