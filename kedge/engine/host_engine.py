"""
Host configuration engine: serializes host machine state into backup staging
and provides reconstitution logic and scripts.
"""

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from kedge.core.models import HostConfigItem
from kedge.core.runner import Runner


class HostEngine:
    @classmethod
    def dump_all(cls, items: List[HostConfigItem], dest_dir: Path) -> Dict[str, Any]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        manifest_items: List[Dict[str, Any]] = []

        for item in items:
            target_path = dest_dir / item.relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if item.source_path == "__user_crontab__":
                res = Runner.run(["crontab", "-l"])
                if res.ok:
                    target_path.write_text(res.stdout, encoding="utf-8")
                    manifest_items.append(item.to_dict())

            elif item.source_path == "__host_bom__":
                bom_data = cls._generate_system_bom(dest_dir)
                target_path.write_text(json.dumps(bom_data, indent=2), encoding="utf-8")
                manifest_items.append(item.to_dict())

            else:
                src = Path(item.source_path)
                if src.is_file():
                    try:
                        shutil.copy2(src, target_path)
                        manifest_items.append(item.to_dict())
                    except (PermissionError, OSError):
                        pass

        # Write manifest.json
        manifest_data = {
            "version": 1,
            "system": platform.platform(),
            "node": platform.node(),
            "items": manifest_items,
        }
        (dest_dir / "manifest.json").write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        # Generate portable reconstitution script
        cls._generate_restore_script(dest_dir, manifest_items)

        return manifest_data

    @classmethod
    def _generate_system_bom(cls, dest_dir: Path) -> Dict[str, Any]:
        bom: Dict[str, Any] = {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python_version": platform.python_version(),
        }

        # Check installed packages
        packages_file = dest_dir / "system" / "packages.txt"
        packages_file.parent.mkdir(parents=True, exist_ok=True)

        if Runner.has_command("dpkg"):
            res = Runner.run(["dpkg", "--get-selections"])
            if res.ok:
                packages_file.write_text(res.stdout, encoding="utf-8")
                bom["package_manager"] = "dpkg"
        elif Runner.has_command("rpm"):
            res = Runner.run(["rpm", "-qa"])
            if res.ok:
                packages_file.write_text(res.stdout, encoding="utf-8")
                bom["package_manager"] = "rpm"
        elif Runner.has_command("apk"):
            res = Runner.run(["apk", "info"])
            if res.ok:
                packages_file.write_text(res.stdout, encoding="utf-8")
                bom["package_manager"] = "apk"
        elif Runner.has_command("pacman"):
            res = Runner.run(["pacman", "-Qe"])
            if res.ok:
                packages_file.write_text(res.stdout, encoding="utf-8")
                bom["package_manager"] = "pacman"

        # Capture active critical sysctl values
        sysctl_values: Dict[str, str] = {}
        for param in [
            "net.core.rmem_max",
            "net.core.wmem_max",
            "net.core.rmem_default",
            "net.core.wmem_default",
            "net.ipv4.ip_forward",
            "fs.file-max",
            "vm.max_map_count",
        ]:
            if Runner.has_command("sysctl"):
                s_res = Runner.run(["sysctl", "-n", param])
                if s_res.ok and s_res.stdout.strip():
                    sysctl_values[param] = s_res.stdout.strip()

        bom["sysctl_live"] = sysctl_values
        return bom

    @classmethod
    def _generate_restore_script(cls, dest_dir: Path, items: List[Dict[str, Any]]) -> None:
        script_path = dest_dir / "restore-host.sh"
        lines = [
            "#!/usr/bin/env bash",
            "# Kedge Automated Host Configuration Reconstitution Script",
            "set -euo pipefail",
            "",
            "BASE_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"",
            "echo \"=== Kedge Host Reconstitution ===\"",
            "echo \"Base directory: $BASE_DIR\"",
            "",
        ]

        for item in items:
            src = item.get("source_path", "")
            rel = item.get("relative_path", "")
            cat = item.get("category", "")

            if src == "__user_crontab__":
                lines.append(f"if [ -f \"$BASE_DIR/{rel}\" ]; then")
                lines.append(f"    echo \"Restoring user crontab...\"")
                lines.append(f"    crontab \"$BASE_DIR/{rel}\" || echo \"Warning: crontab restore failed\"")
                lines.append("fi")
            elif src == "__host_bom__":
                continue
            else:
                lines.append(f"if [ -f \"$BASE_DIR/{rel}\" ]; then")
                lines.append(f"    echo \"[{cat}] Restoring {src}...\"")
                lines.append(f"    sudo mkdir -p \"$(dirname \"{src}\")\"")
                lines.append(f"    sudo cp \"$BASE_DIR/{rel}\" \"{src}\"")
                lines.append("fi")

        lines.append("")
        lines.append("# Reload systemd and sysctl if configs were restored")
        lines.append("if command -v systemctl >/dev/null 2>&1; then")
        lines.append("    echo \"Reloading systemd daemon...\"")
        lines.append("    sudo systemctl daemon-reload || true")
        lines.append("fi")
        lines.append("if command -v sysctl >/dev/null 2>&1; then")
        lines.append("    echo \"Applying sysctl settings...\"")
        lines.append("    sudo sysctl --system || true")
        lines.append("fi")
        lines.append("echo \"Host reconstitution completed.\"")

        script_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            os.chmod(script_path, 0o755)
        except OSError:
            pass
