"""
Comprehensive host machine state and configuration sniffer.
Captures crons, sysctls, shell envs, systemd units, udev rules, network, and system BOM.
"""

import hashlib
import os
import platform
from pathlib import Path
from typing import List, Optional

from kedge.core.models import HostConfigItem
from kedge.core.runner import Runner


class HostSniffer:
    @classmethod
    def sniff_all(
        cls,
        categories: Optional[List[str]] = None,
        custom_paths: Optional[List[str]] = None,
    ) -> List[HostConfigItem]:
        enabled_cats = set(categories or [
            "cron",
            "sysctl",
            "systemd",
            "env",
            "udev",
            "network",
            "system",
        ])

        items: List[HostConfigItem] = []

        if "cron" in enabled_cats:
            items.extend(cls.sniff_crons())

        if "sysctl" in enabled_cats:
            items.extend(cls.sniff_sysctl())

        if "systemd" in enabled_cats:
            items.extend(cls.sniff_systemd())

        if "env" in enabled_cats:
            items.extend(cls.sniff_env())

        if "udev" in enabled_cats:
            items.extend(cls.sniff_udev())

        if "network" in enabled_cats:
            items.extend(cls.sniff_network())

        if "system" in enabled_cats:
            items.extend(cls.sniff_system_bom())

        if custom_paths:
            for cp in custom_paths:
                p = Path(cp).expanduser().resolve()
                if p.exists():
                    items.append(cls._create_item("custom", p, f"custom/{p.name}"))

        return items

    @classmethod
    def sniff_crons(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []

        # 1. User crontab
        res = Runner.run(["crontab", "-l"])
        if res.ok and res.stdout.strip():
            items.append(
                HostConfigItem(
                    category="cron",
                    source_path="__user_crontab__",
                    relative_path="cron/user_crontab",
                    is_file=True,
                    is_dir=False,
                    content_hash=cls._hash_content(res.stdout),
                )
            )

        # 2. System crontab
        sys_crontab = Path("/etc/crontab")
        if sys_crontab.is_file():
            items.append(cls._create_item("cron", sys_crontab, "cron/crontab"))

        # 3. /etc/cron.d/
        cron_d = Path("/etc/cron.d")
        if cron_d.is_dir():
            for f in cron_d.iterdir():
                if f.is_file() and not f.name.startswith("."):
                    items.append(cls._create_item("cron", f, f"cron/cron.d/{f.name}"))

        # 4. /etc/cron.{hourly,daily,weekly,monthly}
        for period in ["hourly", "daily", "weekly", "monthly"]:
            period_dir = Path(f"/etc/cron.{period}")
            if period_dir.is_dir():
                for f in period_dir.iterdir():
                    if f.is_file() and not f.name.startswith("."):
                        items.append(cls._create_item("cron", f, f"cron/cron.{period}/{f.name}"))

        return items

    @classmethod
    def sniff_sysctl(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []

        sysctl_conf = Path("/etc/sysctl.conf")
        if sysctl_conf.is_file():
            items.append(cls._create_item("sysctl", sysctl_conf, "sysctl/sysctl.conf"))

        sysctl_d = Path("/etc/sysctl.d")
        if sysctl_d.is_dir():
            for f in sysctl_d.iterdir():
                if f.is_file() and f.suffix == ".conf":
                    items.append(cls._create_item("sysctl", f, f"sysctl/sysctl.d/{f.name}"))

        return items

    @classmethod
    def sniff_systemd(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []

        system_dir = Path("/etc/systemd/system")
        if system_dir.is_dir():
            try:
                for f in system_dir.iterdir():
                    # Look for non-symlink custom services/timers or custom units
                    if f.is_file() and f.suffix in [".service", ".timer", ".mount", ".path"]:
                        items.append(cls._create_item("systemd", f, f"systemd/system/{f.name}"))
            except (PermissionError, OSError):
                pass

        user_dir = Path.home() / ".config" / "systemd" / "user"
        if user_dir.is_dir():
            try:
                for f in user_dir.iterdir():
                    if f.is_file():
                        items.append(cls._create_item("systemd", f, f"systemd/user/{f.name}"))
            except (PermissionError, OSError):
                pass

        return items

    @classmethod
    def sniff_env(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []

        etc_env = Path("/etc/environment")
        if etc_env.is_file():
            items.append(cls._create_item("env", etc_env, "env/etc_environment"))

        profile_d = Path("/etc/profile.d")
        if profile_d.is_dir():
            try:
                for f in profile_d.iterdir():
                    if f.is_file() and f.suffix == ".sh":
                        items.append(cls._create_item("env", f, f"env/profile.d/{f.name}"))
            except (PermissionError, OSError):
                pass

        home = Path.home()
        for candidate in [".bashrc", ".profile", ".bash_profile", ".zshrc"]:
            p = home / candidate
            if p.is_file():
                items.append(cls._create_item("env", p, f"env/{candidate}"))

        return items

    @classmethod
    def sniff_udev(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []
        udev_d = Path("/etc/udev/rules.d")
        if udev_d.is_dir():
            try:
                for f in udev_d.iterdir():
                    if f.is_file() and f.suffix == ".rules":
                        items.append(cls._create_item("udev", f, f"udev/rules.d/{f.name}"))
            except (PermissionError, OSError):
                pass
        return items

    @classmethod
    def sniff_network(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []

        hosts_file = Path("/etc/hosts")
        if hosts_file.is_file():
            items.append(cls._create_item("network", hosts_file, "network/hosts"))

        netplan_dir = Path("/etc/netplan")
        if netplan_dir.is_dir():
            try:
                for f in netplan_dir.iterdir():
                    if f.is_file() and f.suffix in [".yaml", ".yml"]:
                        items.append(cls._create_item("network", f, f"network/netplan/{f.name}"))
            except (PermissionError, OSError):
                pass

        resolv_conf = Path("/etc/resolv.conf")
        if resolv_conf.is_file():
            items.append(cls._create_item("network", resolv_conf, "network/resolv.conf"))

        return items

    @classmethod
    def sniff_system_bom(cls) -> List[HostConfigItem]:
        items: List[HostConfigItem] = []

        os_release = Path("/etc/os-release")
        if os_release.is_file():
            items.append(cls._create_item("system", os_release, "system/os-release"))

        # Virtual metadata items
        items.append(
            HostConfigItem(
                category="system",
                source_path="__host_bom__",
                relative_path="system/bom.json",
                is_file=True,
                is_dir=False,
            )
        )
        return items

    @staticmethod
    def _create_item(category: str, path: Path, rel_path: str) -> HostConfigItem:
        try:
            stat = path.stat()
            content_hash = HostSniffer._hash_file(path)
            return HostConfigItem(
                category=category,
                source_path=str(path),
                relative_path=rel_path,
                is_file=path.is_file(),
                is_dir=path.is_dir(),
                content_hash=content_hash,
            )
        except (PermissionError, OSError):
            return HostConfigItem(
                category=category,
                source_path=str(path),
                relative_path=rel_path,
                is_file=path.is_file(),
                is_dir=path.is_dir(),
                content_hash=None,
            )

    @staticmethod
    def _hash_file(path: Path) -> Optional[str]:
        try:
            h = hashlib.sha256()
            with open(path, "rb") as f:
                while chunk := f.read(65536):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return None

    @staticmethod
    def _hash_content(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
