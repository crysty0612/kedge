"""
Docker daemon sniffer: discovers active compose stacks, containers, and persistent mounts.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from kedge.core.models import DataVolume, StackInfo
from kedge.core.runner import Runner

EPHEMERAL_MOUNT_PREFIXES = (
    "/var/run",
    "/run",
    "/sys",
    "/proc",
    "/dev",
    "/etc/timezone",
    "/etc/localtime",
    "/etc/resolv.conf",
    "/etc/hosts",
    "/var/cache",
    "/tmp",
    "/var/tmp",
)

BULK_MEDIA_KEYWORDS = {
    "movies",
    "movie",
    "downloads",
    "download",
    "camera",
    "photos",
    "photo",
    "videos",
    "video",
    "music",
    "torrents",
    "torrent",
    "media",
    "library",
    "originals",
    "recordings",
    "roms",
    "games",
    "storage",
    "cache",
    "caches",
    "model",
    "models",
    "weights",
    "thumbnails",
    "thumbs",
    "preview",
    "previews",
    "blobs",
    "chunks",
    "huggingface",
    "lemonade",
    "ollama",
    "vllm",
    "checkpoints",
    "dataset",
    "datasets",
    "files",
}


class DockerSniffer:
    @staticmethod
    def is_docker_available() -> bool:
        if not Runner.has_command("docker"):
            return False
        res = Runner.run(["docker", "info"], timeout=5)
        return res.ok

    @classmethod
    def sniff(cls) -> Tuple[Dict[str, StackInfo], List[DataVolume], List[str]]:
        stacks: Dict[str, StackInfo] = {}
        volumes: List[DataVolume] = []
        warnings: List[str] = []

        if not cls.is_docker_available():
            warnings.append("Docker daemon is not accessible or not running.")
            return stacks, volumes, warnings

        # 1. Fetch all containers
        res = Runner.run(["docker", "ps", "-a", "-q"], timeout=10)
        if not res.ok or not res.stdout.strip():
            return stacks, volumes, warnings

        c_ids = res.stdout.strip().split()
        # Batch inspect in chunks
        chunk_size = 50
        for i in range(0, len(c_ids), chunk_size):
            chunk = c_ids[i:i + chunk_size]
            inspect_res = Runner.run(["docker", "inspect"] + chunk, timeout=15)
            if not inspect_res.ok:
                continue

            try:
                container_data = json.loads(inspect_res.stdout)
            except Exception as e:
                warnings.append(f"Failed to parse docker inspect output: {e}")
                continue

            for container in container_data:
                cls._process_container(container, stacks, volumes)

        # Post-process stacks to ensure compose_file and git status
        for stack_path, stack in list(stacks.items()):
            workdir = Path(stack.workdir)
            if not stack.compose_file:
                for candidate in ["compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"]:
                    cf = workdir / candidate
                    if cf.is_file():
                        stack.compose_file = str(cf)
                        break

            git_dir = workdir / ".git"
            if git_dir.exists():
                stack.is_git_repo = True
                git_remote_res = Runner.run(["git", "remote", "get-url", "origin"], cwd=str(workdir))
                if git_remote_res.ok:
                    stack.git_remote = git_remote_res.stdout.strip()

        return stacks, volumes, warnings

    @classmethod
    def _process_container(
        cls,
        container: dict,
        stacks: Dict[str, StackInfo],
        volumes: List[DataVolume],
    ) -> None:
        c_id = container.get("Id", "")[:12]
        c_name = container.get("Name", "").lstrip("/")
        config = container.get("Config", {})
        labels = config.get("Labels", {}) or {}

        workdir = labels.get("com.docker.compose.project.working_dir")
        compose_file = labels.get("com.docker.compose.project.config_files")
        project_name = labels.get("com.docker.compose.project")

        # 1. Track Stack if compose labels are present
        if workdir and Path(workdir).is_dir():
            if workdir not in stacks:
                s_name = project_name or Path(workdir).name
                stacks[workdir] = StackInfo(
                    name=s_name,
                    workdir=workdir,
                    compose_file=compose_file or "",
                    container_ids=[c_id],
                    container_names=[c_name],
                )
            else:
                stacks[workdir].container_ids.append(c_id)
                stacks[workdir].container_names.append(c_name)

        # 2. Inspect Mounts
        mounts = container.get("Mounts", []) or []
        for mount in mounts:
            if mount.get("Type") != "bind":
                continue
            if not mount.get("RW", False):
                continue

            src = mount.get("Source", "")
            dst = mount.get("Destination", "")
            if not src or not Path(src).exists():
                continue

            # Filter out system, root, and ephemeral mounts
            if src in ("/", "/root", "/var", "/usr", "/bin", "/sbin", "/boot", "/lib", "/lib64") or dst in ("/mnt/host", "/host", "/hostfs"):
                continue
            if any(src.startswith(prefix) for prefix in EPHEMERAL_MOUNT_PREFIXES):
                continue

            # Calculate volume size safely
            vol_size = 0
            src_path = Path(src)
            try:
                if src_path.is_file():
                    vol_size = src_path.stat().st_size
            except OSError:
                pass

            # Detect if volume is a bulk media or storage pool mount
            is_bulk = cls.is_bulk_media_mount(src, dst)

            volumes.append(
                DataVolume(
                    source=src,
                    destination=dst,
                    container_name=c_name,
                    is_rw=True,
                    size_bytes=vol_size,
                    is_bulk_media=is_bulk,
                )
            )

    @classmethod
    def is_bulk_media_mount(cls, src: str, dst: str) -> bool:
        src_p = Path(src)
        dst_p = Path(dst)
        # Check if src is a top-level filesystem mount or root share (e.g. /mnt, /media, /storage)
        if len(src_p.parts) <= 2 and src_p.parts[0] == "/":
            return True

        # Check if path contains .cache or cache
        if "/.cache" in src or "/cache" in src:
            return True

        src_parts = [p.lower() for p in src_p.parts]
        dst_parts = [p.lower() for p in dst_p.parts]

        for p in src_parts:
            if p in BULK_MEDIA_KEYWORDS:
                return True
        for p in dst_parts:
            if p in BULK_MEDIA_KEYWORDS:
                return True

        # Check for media/storage subdirectories
        src_lower = src.lower()
        dst_lower = dst.lower()
        for kw in BULK_MEDIA_KEYWORDS:
            if f"/{kw}/" in f"/{src_lower}/" or f"/{kw}/" in f"/{dst_lower}/":
                return True

        return False
