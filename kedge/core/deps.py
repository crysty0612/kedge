"""
Dependency checker and installer for Kedge.
Detects missing toolchains (git, docker, sqlite3, gpg, tar, gh),
and prompts for permission to install them automatically.
"""

import os
import shutil
import sys
from typing import Dict, List, Optional, Tuple

from kedge.core.runner import Runner
from kedge.core.ui import UI


DEPENDENCIES: Dict[str, Dict[str, str]] = {
    "git": {
        "description": "Git version control for submodules and orchestrator",
        "apt": "git",
        "dnf": "git",
        "pacman": "git",
        "apk": "git",
        "required": True,
    },
    "docker": {
        "description": "Docker engine for container state and discovery",
        "apt": "docker.io",
        "dnf": "docker",
        "pacman": "docker",
        "apk": "docker",
        "required": False,  # Required if managing containers
    },
    "sqlite3": {
        "description": "SQLite3 CLI for database inspection and WAL backups",
        "apt": "sqlite3",
        "dnf": "sqlite",
        "pacman": "sqlite",
        "apk": "sqlite",
        "required": True,
    },
    "gpg": {
        "description": "GNU Privacy Guard for AES-256 state encryption",
        "apt": "gpg",
        "dnf": "gnupg2",
        "pacman": "gnupg",
        "apk": "gnupg",
        "required": True,
    },
    "tar": {
        "description": "Tape archive utility for bundling state",
        "apt": "tar",
        "dnf": "tar",
        "pacman": "tar",
        "apk": "tar",
        "required": True,
    },
    "gh": {
        "description": "GitHub CLI for releases and private submodule provisioning",
        "apt": "gh",
        "dnf": "gh",
        "pacman": "github-cli",
        "apk": "github-cli",
        "required": False,  # Optional: Kedge has direct GitHub REST API fallback
    },
}


class DependencyManager:
    @staticmethod
    def detect_package_manager() -> Optional[str]:
        for pm in ["apt-get", "dnf", "pacman", "apk", "zypper"]:
            if Runner.has_command(pm):
                return pm
        return None

    @classmethod
    def check_dependencies(cls) -> Tuple[List[str], List[str]]:
        """Returns (missing_required, missing_optional)."""
        missing_req = []
        missing_opt = []

        for binary, info in DEPENDENCIES.items():
            if not Runner.has_command(binary):
                if info["required"]:
                    missing_req.append(binary)
                else:
                    missing_opt.append(binary)

        return missing_req, missing_opt

    @classmethod
    def ensure_dependencies(
        cls,
        auto_install: bool = False,
        prompt_user: bool = True,
    ) -> bool:
        missing_req, missing_opt = cls.check_dependencies()

        if not missing_req and not missing_opt:
            return True

        pm = cls.detect_package_manager()

        if missing_req:
            UI.warn(f"Missing required tool(s): {', '.join(missing_req)}")
            for m in missing_req:
                UI.bullet(m, DEPENDENCIES[m]["description"])

        if missing_opt:
            UI.info(f"Missing optional tool(s): {', '.join(missing_opt)}")
            for m in missing_opt:
                UI.bullet(m, DEPENDENCIES[m]["description"])

        if not pm:
            UI.error("No supported package manager (apt-get, dnf, pacman, apk) detected.")
            UI.info("Please install missing tools manually via your system's package manager.")
            return len(missing_req) == 0

        # Build package list for the detected package manager
        packages_to_install = []
        for m in missing_req + missing_opt:
            pkg_name = DEPENDENCIES[m].get(pm.replace("-get", ""))
            if pkg_name and pkg_name not in packages_to_install:
                packages_to_install.append(pkg_name)

        if not packages_to_install:
            return len(missing_req) == 0

        install_cmd = cls._build_install_cmd(pm, packages_to_install)

        do_install = auto_install
        if not auto_install and prompt_user and sys.stdin.isatty():
            print(f"\nKedge can attempt to install missing dependencies automatically.")
            print(f"Command to execute: {UI.BOLD}{install_cmd}{UI.RESET}")
            try:
                choice = input(f"Do you grant permission to install these packages? [Y/n]: ").strip().lower()
                if choice not in ("n", "no"):
                    do_install = True
            except (KeyboardInterrupt, EOFError):
                print()
                do_install = False

        if do_install:
            UI.step(f"Installing missing tools via {pm}...")
            res = Runner.run(install_cmd, shell=True)
            if res.ok:
                UI.success("Dependencies installed successfully.")
                # Re-verify
                still_missing, _ = cls.check_dependencies()
                if not still_missing:
                    return True
                UI.warn(f"Some tools are still missing: {', '.join(still_missing)}")
            else:
                UI.error(f"Installation failed: {res.stderr or res.stdout}")
                UI.info(f"Please run manually: sudo {install_cmd}")

        return len(missing_req) == 0

    @staticmethod
    def _build_install_cmd(pm: str, packages: List[str]) -> str:
        pkg_str = " ".join(packages)
        if pm in ("apt", "apt-get"):
            return f"sudo apt-get update && sudo apt-get install -y {pkg_str}"
        elif pm == "dnf":
            return f"sudo dnf install -y {pkg_str}"
        elif pm == "pacman":
            return f"sudo pacman -S --noconfirm {pkg_str}"
        elif pm == "apk":
            return f"sudo apk add --no-cache {pkg_str}"
        return f"sudo {pm} install {pkg_str}"
