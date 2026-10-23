"""
S3-compatible storage provider for Kedge (AWS S3, Cloudflare R2, MinIO, Backblaze B2).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kedge.core.runner import Runner
from kedge.storage.base import StorageProvider


class S3StorageProvider(StorageProvider):
    def __init__(self, bucket_url: str, endpoint_url: Optional[str] = None):
        self.bucket_url = bucket_url.rstrip("/")
        self.endpoint_url = endpoint_url

    def upload(
        self,
        archive_path: Path,
        tag: str,
        title: str,
        notes: str,
    ) -> Tuple[bool, str]:
        if not archive_path.is_file():
            return False, f"File not found: {archive_path}"

        target = f"{self.bucket_url}/{archive_path.name}"
        cmd = ["aws", "s3", "cp", str(archive_path), target]
        if self.endpoint_url:
            cmd.extend(["--endpoint-url", self.endpoint_url])

        res = Runner.run(cmd, timeout=600)
        if res.ok:
            return True, f"Uploaded archive to {target}"
        return False, f"S3 upload failed: {res.stderr}"

    def download(
        self,
        tag: Optional[str],
        dest_dir: Path,
    ) -> Tuple[bool, Optional[Path], str]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        # S3 download implementation
        return False, None, "S3 download requires explicit object key"

    def list_backups(self) -> List[Dict[str, Any]]:
        return []

    def prune(self, retention_count: int) -> int:
        return 0
