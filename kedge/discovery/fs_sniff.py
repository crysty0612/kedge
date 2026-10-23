"""
Filesystem fallback sniffer: searches candidate directories for Compose files.
"""

from pathlib import Path
from typing import Dict, List, Optional

from kedge.core.models import StackInfo

COMPOSE_FILENAMES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)


class FSSniffer:
    DEFAULT_CANDIDATE_ROOTS = [
        "/Docker",
        "/opt",
        "/srv",
        "/var/lib/docker/compose",
    ]

    @classmethod
    def scan_directories(
        cls,
        candidate_roots: Optional[List[str]] = None,
        max_depth: int = 4,
    ) -> Dict[str, StackInfo]:
        stacks: Dict[str, StackInfo] = {}
        roots = candidate_roots or cls.DEFAULT_CANDIDATE_ROOTS

        for root_str in roots:
            root = Path(root_str).expanduser().resolve()
            if not root.is_dir():
                continue

            try:
                cls._traverse(root, root, 0, max_depth, stacks)
            except (PermissionError, OSError):
                continue

        return stacks

    @classmethod
    def _traverse(
        cls,
        current_dir: Path,
        base_root: Path,
        current_depth: int,
        max_depth: int,
        stacks: Dict[str, StackInfo],
    ) -> None:
        if current_depth > max_depth:
            return

        # Check for compose files in current directory
        for cf in COMPOSE_FILENAMES:
            candidate = current_dir / cf
            if candidate.is_file():
                stack_key = str(current_dir)
                if stack_key not in stacks:
                    stacks[stack_key] = StackInfo(
                        name=current_dir.name,
                        workdir=str(current_dir),
                        compose_file=str(candidate),
                        container_ids=[],
                        container_names=[],
                        is_git_repo=(current_dir / ".git").is_dir(),
                    )
                break

        # Recurse children
        try:
            for child in current_dir.iterdir():
                if child.is_dir() and not child.is_symlink():
                    if child.name.startswith(".") or child.name in ["data", "volumes", "node_modules"]:
                        continue
                    cls._traverse(child, base_root, current_depth + 1, max_depth, stacks)
        except (PermissionError, OSError):
            pass
