"""
Full backup orchestration engine for Kedge:
1. Submodule sync
2. SQLite online atomic hot dumps
3. Host configuration & OS BOM capture
4. Archive packaging with exclusion rules
5. AES-256 GPG encryption
6. Size boundary checks
7. Multi-target upload & retention pruning
"""

import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from kedge.config import ConfigManager
from kedge.core.models import DBTarget, HostConfigItem
from kedge.core.runner import Runner
from kedge.core.ui import UI
from kedge.crypto.gpg import CryptoEngine
from kedge.discovery.docker_sniff import DockerSniffer
from kedge.discovery.host_sniff import HostSniffer
from kedge.discovery.sqlite_sniff import SQLiteSniffer
from kedge.engine.host_engine import HostEngine
from kedge.engine.sqlite_engine import SQLiteEngine
from kedge.gitops.submodules import SubmoduleOrchestrator
from kedge.storage.base import StorageProvider
from kedge.storage.github_release import GitHubReleaseProvider
from kedge.storage.local import LocalStorageProvider
from kedge.storage.s3 import S3StorageProvider


EXCLUDE_PATTERNS = [
    "*.wal",
    "*.shm",
    "*.journal",
    "*.log",
    "querylog.json",
    "*cache*",
    "*filters*",
    "*.bak*",
    "*.tmp",
]


class BackupEngine:
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

    def run(self, dry_run: bool = False, skip_git: bool = False, skip_host: bool = False) -> Tuple[bool, str]:
        timestamp_str = time.strftime("%Y%m%d_%H%M%S")
        tag = f"backup-{timestamp_str}"
        archive_base = f"kedge-state-{timestamp_str}"
        tar_name = f"{archive_base}.tar.gz"
        enc_name = f"{tar_name}.gpg"

        UI.step(f"Starting Kedge Backup Cycle: {tag}")
        if dry_run:
            UI.warn("Running in DRY-RUN mode. No files will be uploaded or deleted.")

        workspace_root = Path(self.config.get("workspace_root", os.getcwd())).resolve()
        data_paths = [Path(p).resolve() for p in self.config.get("data_paths", []) if Path(p).exists()]
        tracked_stacks = self.config.get("tracked_stacks", [])
        tracked_host_items = self.config.get("tracked_host_items", [])
        custom_host_paths = self.config.get("custom_host_paths", [])

        # Step 1: Git Submodule Sync
        if not skip_git and self.config.get("gitops", {}).get("enabled", True):
            UI.step("[1/6] Synchronizing Git Submodules")
            if (workspace_root / ".git").exists():
                ok, logs = SubmoduleOrchestrator.sync_submodules(workspace_root, timestamp_str)
                for log in logs:
                    UI.info(log)
            else:
                UI.info("Workspace root is not a git repository. Skipping git sync.")

        # Staging directory for consistent assembly
        with tempfile.TemporaryDirectory(prefix="kedge_stage_") as stage_dir_str:
            stage_dir = Path(stage_dir_str)
            db_stage_dir = stage_dir / "sqlite_dumps"
            host_stage_dir = stage_dir / "host_state"
            secrets_stage_dir = stage_dir / "stack_secrets"

            # Step 2: Online Atomic SQLite Hot Backups
            UI.step("[2/6] Discovering & Hot-Dumping SQLite Databases")
            discovered_dbs: List[DBTarget] = []
            for dp in data_paths:
                discovered_dbs.extend(SQLiteSniffer.scan_path(str(dp), base_root=str(workspace_root)))

            UI.info(f"Discovered {len(discovered_dbs)} active SQLite database(s).")
            backed_up_db_rel_paths: List[str] = []

            for db in discovered_dbs:
                UI.info(f"Hot-dumping: {db.relative_path} (WAL={db.has_wal})")
                if not dry_run:
                    ok, dest_path, msg = SQLiteEngine.backup_database(db, db_stage_dir)
                    if not ok:
                        UI.error(f"Failed to snapshot {db.relative_path}: {msg}")
                        return False, f"Database backup failed for {db.relative_path}: {msg}"
                backed_up_db_rel_paths.append(db.relative_path)

            # Step 3: Host Configuration & OS State Sniffing
            UI.step("[3/6] Capturing Host Machine Configurations & OS BOM")
            host_items = []
            if not skip_host:
                host_items = HostSniffer.sniff_all(
                    categories=tracked_host_items,
                    custom_paths=custom_host_paths,
                )
                UI.info(f"Sniffed {len(host_items)} host configuration item(s).")
                if not dry_run:
                    HostEngine.dump_all(host_items, host_stage_dir)

            # Step 4: Archive Stack Secrets (.env / keys) across all stacks
            UI.step("[4/6] Isolating Container Secrets (.env / keys)")
            secret_mappings: List[Dict[str, str]] = []
            stack_dirs: Dict[str, Path] = {}

            containers_dir = workspace_root / self.config.get("containers_dir", "containers")
            if containers_dir.is_dir():
                for child in containers_dir.iterdir():
                    if child.is_dir():
                        stack_dirs[child.name] = child

            # Also inspect active host stacks to discover secrets in non-workspace locations (e.g. CasaOS, custom paths)
            docker_stacks, _, _ = DockerSniffer.sniff()
            for s in docker_stacks.values():
                if s.workdir and Path(s.workdir).is_dir():
                    stack_dirs[s.name] = Path(s.workdir)

            for stack_name, s_dir in stack_dirs.items():
                found_secrets: List[Path] = []
                for pat in [".env", ".env.*", "secrets.yaml"]:
                    for sf in s_dir.glob(pat):
                        if sf.is_file() and sf not in found_secrets:
                            found_secrets.append(sf)
                for pat in ["*.key", "*.pem"]:
                    for sf in s_dir.glob(pat):
                        if sf.is_file() and sf not in found_secrets:
                            found_secrets.append(sf)

                for sf in found_secrets:
                    try:
                        rel_s = str(sf.relative_to(s_dir))
                    except ValueError:
                        rel_s = sf.name
                    dest_s = secrets_stage_dir / stack_name / rel_s
                    dest_s.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sf, dest_s)
                    stat = sf.stat()
                    archive_path = f"manifest/secrets/{stack_name}/{rel_s}"
                    secret_mappings.append({
                        "stack_name": stack_name,
                        "host_workdir": str(s_dir),
                        "filename": rel_s,
                        "archive_path": archive_path,
                        "uid": stat.st_uid,
                        "gid": stat.st_gid,
                        "mode": stat.st_mode,
                    })
                    UI.info(f"Preserved secret: {stack_name}/{rel_s}")

            # Build volume mappings for multi-disk & scattered hierarchies
            volume_mappings: List[Dict[str, Any]] = []
            for dp in data_paths:
                if dp.exists():
                    if dp.is_relative_to(workspace_root):
                        arcname = str(dp.relative_to(workspace_root))
                        in_ws = True
                    else:
                        arcname = "volumes/" + str(dp).lstrip("/")
                        in_ws = False
                    volume_mappings.append({
                        "archive_prefix": arcname,
                        "host_path": str(dp),
                        "is_in_workspace": in_ws,
                    })

            # Manifest metadata
            manifest = {
                "tag": tag,
                "timestamp": timestamp_str,
                "workspace_root": str(workspace_root),
                "data_paths": [str(p) for p in data_paths],
                "volume_mappings": volume_mappings,
                "secret_mappings": secret_mappings,
                "databases": [d.to_dict() for d in discovered_dbs],
                "host_items_count": len(host_items),
            }
            (stage_dir / "kedge-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            # Step 5: Packaging & Encrypting
            UI.step("[5/6] Packaging & Encrypting Data Volumes")
            tar_path = stage_dir / tar_name
            enc_path = stage_dir / enc_name

            if dry_run:
                UI.success("Dry run completed. Staging and backup checks passed.")
                return True, "Dry-run completed successfully"

            # Build tarball
            self._create_tarball(
                tar_path=tar_path,
                workspace_root=workspace_root,
                data_paths=data_paths,
                stage_dir=stage_dir,
                exclude_db_rel_paths=backed_up_db_rel_paths,
            )

            # Encrypt with AES-256
            enc_cfg = self.config.get("encryption", {})
            key_file = CryptoEngine.ensure_key_file(enc_cfg.get("key_file", "~/.config/kedge/kedge.key"))
            ok, enc_msg = CryptoEngine.encrypt(tar_path, enc_path, key_file)
            if not ok:
                UI.error(enc_msg)
                return False, enc_msg

            # Step 6: Quota Enforcement & Storage Upload
            UI.step("[6/6] Enforcing Quota Ceiling & Remote Publishing")
            storage = self._get_storage_provider()
            max_mb = self.config.get("storage", {}).get("max_asset_size_mb", 1800)

            ok, size_mb, limit_msg = storage.check_size_limit(enc_path, max_mb=max_mb)
            if not ok:
                UI.error(limit_msg)
                return False, limit_msg
            elif "Warning" in limit_msg:
                UI.warn(limit_msg)
            else:
                UI.success(limit_msg)

            upload_ok, upload_msg = storage.upload(
                archive_path=enc_path,
                tag=tag,
                title=f"Kedge State Snapshot {timestamp_str}",
                notes=f"Automated encrypted disaster recovery snapshot.\nSize: {size_mb:.1f} MB\nHost Items: {len(host_items)}\nDatabases: {len(discovered_dbs)}",
            )
            if not upload_ok:
                UI.error(upload_msg)
                return False, upload_msg

            UI.success(upload_msg)

            # Pruning
            retention = self.config.get("storage", {}).get("retention_count", 14)
            pruned_count = storage.prune(retention)
            if pruned_count > 0:
                UI.info(f"Rotated and pruned {pruned_count} expired release(s).")

            UI.success(f"Disaster Recovery Backup Lifecycle Completed: {tag}")
            return True, f"Backup successful: {tag}"

    @classmethod
    def _create_tarball(
        cls,
        tar_path: Path,
        workspace_root: Path,
        data_paths: List[Path],
        stage_dir: Path,
        exclude_db_rel_paths: List[str],
    ) -> None:
        exclude_set = set(exclude_db_rel_paths)

        def tar_filter(tarinfo: tarfile.TarInfo) -> Optional[tarfile.TarInfo]:
            # Filter ephemeral auxiliary patterns
            for pat in EXCLUDE_PATTERNS:
                if pat.startswith("*") and pat.endswith("*"):
                    term = pat.strip("*")
                    if term in tarinfo.name:
                        return None
                elif pat.startswith("*"):
                    if tarinfo.name.endswith(pat[1:]):
                        return None
                elif tarinfo.name == pat or tarinfo.name.endswith("/" + pat):
                    return None

            # Filter raw database paths from direct volume stream
            for exc_rel in exclude_set:
                if tarinfo.name.endswith(exc_rel):
                    return None
            return tarinfo

        with tarfile.open(tar_path, "w:gz") as tar:
            # Add persistent data paths
            for dp in data_paths:
                if dp.exists():
                    if dp.is_relative_to(workspace_root):
                        arcname = str(dp.relative_to(workspace_root))
                    else:
                        arcname = "volumes/" + str(dp).lstrip("/")
                    tar.add(str(dp), arcname=arcname, filter=tar_filter)

            # Add staged atomic SQLite dumps
            sqlite_stage = stage_dir / "sqlite_dumps"
            if sqlite_stage.is_dir():
                for item in sqlite_stage.iterdir():
                    tar.add(str(item), arcname=item.name)

            # Add staged host state & reconstitution scripts
            host_stage = stage_dir / "host_state"
            if host_stage.is_dir():
                tar.add(str(host_stage), arcname="manifest/host")

            # Add stack secrets
            secrets_stage = stage_dir / "stack_secrets"
            if secrets_stage.is_dir():
                tar.add(str(secrets_stage), arcname="manifest/secrets")

            # Add manifest
            manifest_file = stage_dir / "kedge-manifest.json"
            if manifest_file.is_file():
                tar.add(str(manifest_file), arcname="kedge-manifest.json")
