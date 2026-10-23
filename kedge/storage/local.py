"""
Local directory storage provider for Kedge archives (NFS, SMB, external drive, local storage).
"""

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kedge.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    def __init__(self, target_dir: str):
        target_path = Path(target_dir).expanduser().resolve()
        try:
            target_path.mkdir(parents=True, exist_ok=True)
            self.target_dir = target_path
        except (PermissionError, OSError):
            fallback = Path.home() / ".local" / "share" / "kedge" / "backups"
            fallback.mkdir(parents=True, exist_ok=True)
            self.target_dir = fallback

        self.metadata_file = self.target_dir / "index.json"

    def _load_index(self) -> List[Dict[str, Any]]:
        if not self.metadata_file.is_file():
            return []
        try:
            return json.loads(self.metadata_file.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_index(self, index_data: List[Dict[str, Any]]) -> None:
        self.metadata_file.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

    def upload(
        self,
        archive_path: Path,
        tag: str,
        title: str,
        notes: str,
    ) -> Tuple[bool, str]:
        if not archive_path.is_file():
            return False, f"Source archive not found: {archive_path}"

        dest_file = self.target_dir / archive_path.name
        try:
            shutil.copy2(archive_path, dest_file)
            index = self._load_index()
            record = {
                "tag": tag,
                "title": title,
                "notes": notes,
                "filename": dest_file.name,
                "size_bytes": dest_file.stat().st_size,
                "timestamp": int(time.time()),
            }
            # Insert at beginning (newest first)
            index.insert(0, record)
            self._save_index(index)
            return True, f"Saved archive to local storage: {dest_file}"
        except Exception as e:
            return False, f"Failed to save local archive: {e}"

    def download(
        self,
        tag: Optional[str],
        dest_dir: Path,
    ) -> Tuple[bool, Optional[Path], str]:
        index = self._load_index()
        dest_dir.mkdir(parents=True, exist_ok=True)

        target_record: Optional[Dict[str, Any]] = None
        if tag:
            for item in index:
                if item.get("tag") == tag:
                    target_record = item
                    break
        else:
            if index:
                target_record = index[0]

        if not target_record:
            # Fallback to scanning folder directly for newest .gpg or .tar.gz
            candidates = sorted(
                self.target_dir.glob("*.gpg"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                candidates = sorted(
                    self.target_dir.glob("*.tar.gz"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            if not candidates:
                return False, None, "No local backups found"
            source_file = candidates[0]
        else:
            source_file = self.target_dir / target_record["filename"]

        if not source_file.is_file():
            return False, None, f"Backup file {source_file} missing from disk"

        dest_file = dest_dir / source_file.name
        shutil.copy2(source_file, dest_file)
        return True, dest_file, f"Downloaded backup {source_file.name}"

    def list_backups(self) -> List[Dict[str, Any]]:
        return self._load_index()

    def prune(self, retention_count: int) -> int:
        index = self._load_index()
        if len(index) <= retention_count:
            return 0

        keep = index[:retention_count]
        remove = index[retention_count:]
        deleted_count = 0

        for item in remove:
            f_path = self.target_dir / item.get("filename", "")
            if f_path.is_file():
                try:
                    f_path.unlink()
                    deleted_count += 1
                except OSError:
                    pass

        self._save_index(keep)
        return deleted_count
