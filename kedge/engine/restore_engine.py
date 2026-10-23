"""
Disaster recovery and state restoration engine for Kedge.
Supports sandboxed non-destructive restoration, dry-run inspection, and SQLite integrity validation.
"""

import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from kedge.config import ConfigManager
from kedge.core.runner import Runner
from kedge.core.ui import UI
from kedge.crypto.gpg import CryptoEngine
from kedge.engine.sqlite_engine import SQLiteEngine
from kedge.storage.base import StorageProvider
from kedge.storage.github_release import GitHubReleaseProvider
from kedge.storage.local import LocalStorageProvider
from kedge.storage.s3 import S3StorageProvider


class RestoreEngine:
    def __init__(self, config_manager: ConfigManager):
        self.cfg_mgr = config_manager
        self.config = config_manager.data

    def _get_storage_provider(self) -> StorageProvider:
        storage_cfg = self.config.get("storage", {})
        backend = storage_cfg.get("backend", "local")

        if backend == "github":
            repo = storage_cfg.get("github_repo")
            if not repo:
                raise ValueError("storage.github_repo must be specified when backend is 'github'")
            return GitHubReleaseProvider(repo=repo)
        elif backend == "s3":
            bucket = storage_cfg.get("s3_bucket", "")
            endpoint = storage_cfg.get("s3_endpoint")
            return S3StorageProvider(bucket_url=bucket, endpoint_url=endpoint)
        else:
            local_path = storage_cfg.get("local_path", "/var/backups/kedge")
            return LocalStorageProvider(target_dir=local_path)

    def run(
        self,
        tag: Optional[str] = None,
        target_dir: Optional[str] = None,
        dry_run: bool = False,
        launch_containers: bool = False,
    ) -> Tuple[bool, str]:
        UI.step("Initiating Kedge Disaster Recovery Restore Engine")
        if dry_run:
            UI.warn("Running in DRY-RUN mode. No files on the host will be overwritten.")

        storage = self._get_storage_provider()
        workspace_root = Path(target_dir).resolve() if target_dir else Path(self.config.get("workspace_root", os.getcwd())).resolve()

        with tempfile.TemporaryDirectory(prefix="kedge_restore_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)

            # Step 1: Download Remote Archive
            UI.step("[1/4] Downloading Remote State Artifact")
            dl_ok, enc_file, dl_msg = storage.download(tag=tag, dest_dir=tmp_dir)
            if not dl_ok or not enc_file:
                UI.error(dl_msg)
                return False, dl_msg

            UI.success(f"Acquired artifact: {enc_file.name}")

            # Step 2: Decrypt Archive
            UI.step("[2/4] Decrypting State Payload (AES-256)")
            enc_cfg = self.config.get("encryption", {})
            key_file = Path(enc_cfg.get("key_file", "~/.config/kedge/kedge.key")).expanduser().resolve()
            dec_tar = tmp_dir / "unpacked_state.tar.gz"

            dec_ok, dec_msg = CryptoEngine.decrypt(enc_file, dec_tar, key_file)
            if not dec_ok:
                UI.error(dec_msg)
                return False, dec_msg

            UI.success("Decryption successful.")

            # Step 3: Inspect & Extract
            is_sandbox = target_dir is not None
            mode_desc = f"Sandbox Target: {workspace_root}" if is_sandbox else f"Host Target: {workspace_root}"
            UI.step(f"[3/4] Extracting State Payload ({mode_desc})")

            with tarfile.open(dec_tar, "r:gz") as tar:
                members = tar.getmembers()
                UI.info(f"Archive contains {len(members)} entries.")

                # Read manifest if present in archive
                manifest_data: Dict[str, Any] = {}
                try:
                    m_file = tar.extractfile("kedge-manifest.json")
                    if m_file:
                        manifest_data = json.loads(m_file.read().decode("utf-8"))
                except Exception:
                    pass

                secret_mappings = manifest_data.get("secret_mappings", [])

                if dry_run:
                    UI.info("Dry-run preview of files in archive and intended destinations:")
                    for m in members[:25]:
                        dest_desc = str(workspace_root / m.name)
                        if not is_sandbox:
                            if m.name.startswith("volumes/"):
                                dest_desc = "/" + m.name.removeprefix("volumes/")
                            elif m.name.startswith("manifest/secrets/"):
                                for sm in secret_mappings:
                                    if m.name == sm.get("archive_path"):
                                        dest_desc = str(Path(sm.get("host_workdir", "")) / sm.get("filename", ""))
                                        break
                        UI.bullet(m.name, f"-> {dest_desc} ({UI.format_bytes(m.size)})")
                    if len(members) > 25:
                        UI.info(f"... and {len(members) - 25} more items.")
                    UI.success("Dry-run restore inspection complete. No changes made.")
                    return True, "Dry-run restore complete"

                if is_sandbox:
                    workspace_root.mkdir(parents=True, exist_ok=True)
                    tar.extractall(path=workspace_root)
                else:
                    # Live host restoration: restore volumes to actual host disks and secrets to stack workdirs
                    for m in members:
                        if m.name.startswith("volumes/"):
                            rel = m.name.removeprefix("volumes/")
                            dest_path = Path("/" + rel)
                            if m.isdir():
                                dest_path.mkdir(parents=True, exist_ok=True)
                            elif m.isfile():
                                dest_path.parent.mkdir(parents=True, exist_ok=True)
                                extracted_stream = tar.extractfile(m)
                                if extracted_stream:
                                    with open(dest_path, "wb") as df:
                                        shutil.copyfileobj(extracted_stream, df)
                                    try:
                                        dest_path.chmod(m.mode)
                                    except OSError:
                                        pass
                        elif m.name.startswith("manifest/secrets/"):
                            found_secret = False
                            for sm in secret_mappings:
                                if m.name == sm.get("archive_path"):
                                    dest_path = Path(sm.get("host_workdir", "")) / sm.get("filename", "")
                                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                                    extracted_stream = tar.extractfile(m)
                                    if extracted_stream:
                                        with open(dest_path, "wb") as df:
                                            shutil.copyfileobj(extracted_stream, df)
                                        try:
                                            dest_path.chmod(0o600)
                                        except OSError:
                                            pass
                                    found_secret = True
                                    break
                            if not found_secret:
                                tar.extract(m, path=workspace_root)
                        else:
                            tar.extract(m, path=workspace_root)

            # Step 4: Verify Database Integrity in Restored Destination
            UI.step("[4/4] Validating Integrity of Restored Databases")
            verified_count = 0
            for current_dir, _, files in os.walk(workspace_root):
                dir_path = Path(current_dir)
                for f in files:
                    fp = dir_path / f
                    if f.endswith((".sqlite", ".sqlite3", ".db")):
                        ok, check_msg = SQLiteEngine.check_integrity(fp)
                        if ok:
                            UI.success(f"Database integrity verified: {fp.relative_to(workspace_root)}")
                            verified_count += 1
                        else:
                            UI.warn(f"Integrity check issue for {fp}: {check_msg}")

            UI.success(f"Verified {verified_count} restored SQLite database(s).")

            # Check if host reconstitution script was extracted
            restore_host_script = workspace_root / "manifest" / "host" / "restore-host.sh"
            if restore_host_script.is_file():
                UI.info(f"Host reconstitution script ready at: {restore_host_script}")

            # Optional: Launch container stacks
            if launch_containers and not dry_run:
                UI.step("Launching Restored Container Stacks")
                containers_dir = workspace_root / self.config.get("containers_dir", "containers")
                if containers_dir.is_dir():
                    for stack_dir in containers_dir.iterdir():
                        if stack_dir.is_dir():
                            for cf in ["compose.yaml", "compose.yml", "docker-compose.yml"]:
                                c_path = stack_dir / cf
                                if c_path.is_file():
                                    UI.info(f"Starting stack: {stack_dir.name}")
                                    Runner.run(["docker", "compose", "-f", str(c_path), "up", "-d"])
                                    break

            UI.success("Disaster recovery state restoration completed successfully.")
            return True, "Restore completed successfully"
