"""
Base abstract storage provider for Kedge disaster recovery archives.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class StorageProvider(ABC):
    @abstractmethod
    def upload(
        self,
        archive_path: Path,
        tag: str,
        title: str,
        notes: str,
    ) -> Tuple[bool, str]:
        pass

    @abstractmethod
    def download(
        self,
        tag: Optional[str],
        dest_dir: Path,
    ) -> Tuple[bool, Optional[Path], str]:
        pass

    @abstractmethod
    def list_backups(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    def prune(self, retention_count: int) -> int:
        pass

    def check_size_limit(self, file_path: Path, max_mb: int = 1800) -> Tuple[bool, float, str]:
        if not file_path.is_file():
            return False, 0.0, f"File not found: {file_path}"
        size_bytes = file_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)

        if size_mb > max_mb:
            return (
                False,
                size_mb,
                f"Asset size ({size_mb:.1f} MB) exceeds maximum boundary of {max_mb} MB. Upload aborted.",
            )
        elif size_mb > (max_mb * 0.75):
            return (
                True,
                size_mb,
                f"Warning: Asset size ({size_mb:.1f} MB) is approaching safety limit of {max_mb} MB.",
            )
        return True, size_mb, f"Asset size ({size_mb:.1f} MB) is within limits."
