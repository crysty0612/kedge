"""
Non-invasive SQLite database discovery via magic-byte header inspection.
Reads the first 16 bytes: b"SQLite format 3\\0".
Identifies WAL and SHM frame files.
"""

import os
from pathlib import Path
from typing import List, Optional

from kedge.core.models import DBTarget

SQLITE_HEADER = b"SQLite format 3\x00"


class SQLiteSniffer:
    @staticmethod
    def is_sqlite_file(file_path: Path) -> bool:
        try:
            if not file_path.is_file() or file_path.is_symlink():
                return False
            if file_path.stat().st_size < 16:
                return False
            with open(file_path, "rb") as f:
                header = f.read(16)
                return header == SQLITE_HEADER
        except (PermissionError, OSError):
            return False

    @classmethod
    def scan_path(
        cls,
        root_path: str,
        base_root: Optional[str] = None,
        max_depth: int = 4,
    ) -> List[DBTarget]:
        root = Path(root_path).expanduser().resolve()
        if not root.exists():
            return []

        base = Path(base_root).expanduser().resolve() if base_root else root
        found: List[DBTarget] = []

        if root.is_file():
            if cls.is_sqlite_file(root):
                found.append(cls._create_target(root, base))
            return found

        root_depth = len(root.parts)
        for current_dir, dirs, files in os.walk(root):
            dir_path = Path(current_dir)
            # Prevent runaway recursion on deep or massive storage trees
            if len(dir_path.parts) - root_depth >= max_depth:
                dirs.clear()

            # Filter out non-state bulk directories from recursing
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d.lower() not in (
                    "camera", "movies", "downloads", "music", "photos",
                    "videos", "media", "cache", "node_modules", ".git"
                )
            ]

            for filename in files:
                if filename.endswith(("-wal", "-shm", "-journal", ".tmp", ".log")):
                    continue
                file_path = dir_path / filename
                if cls.is_sqlite_file(file_path):
                    found.append(cls._create_target(file_path, base))

        return found

    @classmethod
    def _create_target(cls, db_path: Path, base: Path) -> DBTarget:
        stat = db_path.stat()
        wal_path = db_path.with_name(db_path.name + "-wal")
        shm_path = db_path.with_name(db_path.name + "-shm")

        try:
            rel_path = str(db_path.relative_to(base))
        except ValueError:
            # Preserve full host hierarchy with 'volumes/' prefix to prevent collisions
            rel_path = "volumes/" + str(db_path).lstrip("/")

        return DBTarget(
            path=str(db_path),
            size_bytes=stat.st_size,
            relative_path=rel_path,
            format="sqlite3",
            has_wal=wal_path.is_file(),
            has_shm=shm_path.is_file(),
        )
