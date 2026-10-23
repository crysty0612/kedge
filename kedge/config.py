"""
Configuration loader, saver, and schema validator for Kedge.
Supports JSON and YAML seamlessly without requiring external packages.
"""

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "workspace_root": "",
    "containers_dir": "containers",
    "data_paths": [],
    "tracked_stacks": [],
    "tracked_host_items": [
        "crons",
        "sysctl",
        "systemd",
        "env",
        "udev",
        "network",
        "system",
    ],
    "custom_host_paths": [],
    "encryption": {
        "enabled": True,
        "algorithm": "AES256",
        "key_file": "~/.config/kedge/kedge.key",
    },
    "storage": {
        "backend": "local",  # local, github, s3
        "local_path": "/var/backups/kedge",
        "github_repo": "",
        "retention_count": 14,
        "max_asset_size_mb": 1800,
    },
    "gitops": {
        "enabled": True,
        "repo_prefix": "stack",
        "parent_repo": "orchestrator",
        "komodo_periphery": False,
        "periphery_port": 8120,
    },
    "scheduler": {
        "enabled": False,
        "cron_expression": "0 3 * * *",
    },
}


class ConfigManager:
    CONFIG_NAMES = [".kedge.yaml", ".kedge.yml", ".kedge.json"]
    GLOBAL_CONFIG_DIR = Path.home() / ".config" / "kedge"

    def __init__(self, explicit_path: Optional[str] = None):
        self.config_path: Path = self._resolve_config_path(explicit_path)
        self.data: Dict[str, Any] = {}
        self.load()

    def _resolve_config_path(self, explicit_path: Optional[str] = None) -> Path:
        if explicit_path:
            return Path(explicit_path).expanduser().resolve()

        # Check local directory first
        cwd = Path.cwd()
        for name in self.CONFIG_NAMES:
            candidate = cwd / name
            if candidate.is_file():
                return candidate.resolve()

        # Check global user directory
        for name in self.CONFIG_NAMES:
            candidate = self.GLOBAL_CONFIG_DIR / name
            if candidate.is_file():
                return candidate.resolve()

        # Default fallback
        return cwd / ".kedge.yaml"

    @property
    def exists(self) -> bool:
        return self.config_path.is_file()

    def load(self) -> Dict[str, Any]:
        if not self.exists:
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            return self.data

        content = self.config_path.read_text(encoding="utf-8")
        if self.config_path.suffix in [".yaml", ".yml"]:
            try:
                import yaml
                loaded = yaml.safe_load(content) or {}
            except ImportError:
                # Built-in lightweight fallback parser for basic YAML/JSON
                try:
                    loaded = json.loads(content)
                except Exception:
                    loaded = self._parse_simple_yaml(content)
        else:
            loaded = json.loads(content)

        merged = copy.deepcopy(DEFAULT_CONFIG)
        self._deep_update(merged, loaded)
        self.data = merged
        return self.data

    def save(self) -> Path:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        content = self._dump_simple_yaml(self.data)
        self.config_path.write_text(content, encoding="utf-8")
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        return self.config_path

    def get_value(self, key_path: str) -> Any:
        if key_path in ("remote", "origin", "git_remote"):
            return self.data.get("gitops", {}).get("remote_url") or self.data.get("storage", {}).get("github_repo")
        if key_path in ("repo", "github_repo"):
            return self.data.get("storage", {}).get("github_repo")
        if key_path in ("cron", "schedule"):
            return self.data.get("scheduler", {}).get("cron_expression")
        if key_path == "retention":
            return self.data.get("storage", {}).get("retention_count")

        parts = key_path.split(".")
        curr: Any = self.data
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                return None
        return curr

    def set_value(self, key_path: str, value_str: str) -> bool:
        val: Any = value_str
        if value_str.lower() in ("true", "yes", "on"):
            val = True
        elif value_str.lower() in ("false", "no", "off"):
            val = False
        elif value_str.isdigit():
            val = int(value_str)

        if key_path in ("remote", "origin"):
            self.data.setdefault("gitops", {})["remote_url"] = value_str
            if "github.com" in value_str:
                clean_repo = value_str.split("github.com", 1)[1].lstrip("/:").removesuffix(".git")
                self.data.setdefault("storage", {})["github_repo"] = clean_repo
            self.save()
            return True

        if key_path in ("repo", "github_repo"):
            self.data.setdefault("storage", {})["github_repo"] = value_str
            self.save()
            return True

        if key_path in ("cron", "schedule"):
            self.data.setdefault("scheduler", {})["cron_expression"] = value_str
            self.save()
            return True

        if key_path == "retention":
            self.data.setdefault("storage", {})["retention_count"] = int(value_str) if str(value_str).isdigit() else 14
            self.save()
            return True

        parts = key_path.split(".")
        curr = self.data
        for p in parts[:-1]:
            if p not in curr or not isinstance(curr[p], dict):
                curr[p] = {}
            curr = curr[p]

        curr[parts[-1]] = val
        self.save()
        return True

    def reset(self) -> None:
        self.data = copy.deepcopy(DEFAULT_CONFIG)
        self.save()

    def get_flattened(self) -> Dict[str, Any]:
        flat: Dict[str, Any] = {}
        def _flatten(d: dict, prefix: str = ""):
            for k, v in d.items():
                p = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, p)
                else:
                    flat[p] = v
        _flatten(self.data)
        return flat

    @staticmethod
    def _deep_update(target: dict, source: dict) -> None:
        for k, v in source.items():
            if isinstance(v, dict) and k in target and isinstance(target[k], dict):
                ConfigManager._deep_update(target[k], v)
            else:
                target[k] = v

    @staticmethod
    def _parse_simple_yaml(text: str) -> dict:
        """Simple YAML fallback parser for basic key-value and list structures."""
        res: dict = {}
        curr_section: Optional[str] = None
        curr_list: Optional[list] = None
        list_key: Optional[str] = None

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("- "):
                val = line[2:].strip().strip("\"'")
                if curr_list is None and curr_section and curr_section in res:
                    if res[curr_section] == {}:
                        res[curr_section] = []
                        curr_list = res[curr_section]
                if curr_list is not None:
                    curr_list.append(val)
                continue

            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip()
                val = val.strip()

                indent = len(raw_line) - len(raw_line.lstrip())

                if not val:
                    # Could be start of section or list
                    if indent == 0:
                        curr_section = key
                        res[key] = {}
                        curr_list = None
                    else:
                        if curr_section and isinstance(res.get(curr_section), dict):
                            res[curr_section][key] = []
                            curr_list = res[curr_section][key]
                else:
                    clean_val: Any = val.strip("\"'")
                    if clean_val.lower() == "true":
                        clean_val = True
                    elif clean_val.lower() == "false":
                        clean_val = False
                    elif clean_val.isdigit():
                        clean_val = int(clean_val)

                    if indent == 0:
                        curr_section = None
                        res[key] = clean_val
                        curr_list = None
                    elif curr_section:
                        res[curr_section][key] = clean_val
                        curr_list = None

        return res

    @staticmethod
    def _dump_simple_yaml(data: dict, indent: int = 0) -> str:
        lines = []
        prefix = "  " * indent
        for k, v in data.items():
            if isinstance(v, dict):
                lines.append(f"{prefix}{k}:")
                lines.append(ConfigManager._dump_simple_yaml(v, indent + 1))
            elif isinstance(v, list):
                lines.append(f"{prefix}{k}:")
                for item in v:
                    lines.append(f"{prefix}  - \"{item}\"")
            elif isinstance(v, bool):
                lines.append(f"{prefix}{k}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{prefix}{k}: {v}")
            else:
                lines.append(f"{prefix}{k}: \"{v}\"")
        return "\n".join(lines) + "\n"
