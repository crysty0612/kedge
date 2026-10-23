"""
Data models and configuration definitions for Kedge.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class StackInfo:
    name: str
    workdir: str
    compose_file: str
    container_ids: List[str] = field(default_factory=list)
    container_names: List[str] = field(default_factory=list)
    is_git_repo: bool = False
    git_remote: Optional[str] = None
    submodule_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "workdir": self.workdir,
            "compose_file": self.compose_file,
            "container_ids": self.container_ids,
            "container_names": self.container_names,
            "is_git_repo": self.is_git_repo,
            "git_remote": self.git_remote,
            "submodule_path": self.submodule_path,
        }


@dataclass
class DataVolume:
    source: str
    destination: str
    container_name: str
    is_rw: bool = True
    size_bytes: int = 0
    is_bulk_media: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "destination": self.destination,
            "container_name": self.container_name,
            "is_rw": self.is_rw,
            "size_bytes": self.size_bytes,
            "is_bulk_media": self.is_bulk_media,
        }


@dataclass
class DBTarget:
    path: str
    size_bytes: int
    relative_path: str
    format: str = "sqlite3"
    has_wal: bool = False
    has_shm: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "relative_path": self.relative_path,
            "format": self.format,
            "has_wal": self.has_wal,
            "has_shm": self.has_shm,
        }


@dataclass
class HostConfigItem:
    category: str  # cron, sysctl, systemd, env, udev, network, system
    source_path: str
    relative_path: str
    is_file: bool = True
    is_dir: bool = False
    content_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "source_path": self.source_path,
            "relative_path": self.relative_path,
            "is_file": self.is_file,
            "is_dir": self.is_dir,
            "content_hash": self.content_hash,
        }


@dataclass
class DiscoveredTopology:
    stacks: Dict[str, StackInfo] = field(default_factory=dict)
    volumes: List[DataVolume] = field(default_factory=list)
    databases: List[DBTarget] = field(default_factory=list)
    host_configs: List[HostConfigItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stacks": {k: v.to_dict() for k, v in self.stacks.items()},
            "volumes": [v.to_dict() for v in self.volumes],
            "databases": [d.to_dict() for d in self.databases],
            "host_configs": [h.to_dict() for h in self.host_configs],
            "warnings": self.warnings,
        }
