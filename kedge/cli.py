"""
Command-line interface and subcommand dispatcher for Kedge.
Commands:
  kedge init      - Interactive onboarding & configuration wizard (workspace, remote git, komodo, cron)
  kedge discover  - Scan Docker daemon, compose stacks, bind mounts, SQLite DBs, and host configs
  kedge scan      - Alias for kedge discover
  kedge add       - Add stack, data path, or host config to tracking
  kedge drop      - Remove item from tracking
  kedge status    - Display tracked inventory, remote release status, quota, and drift
  kedge config    - View or modify Kedge configuration settings (remote, schedule, retention, etc.)
  kedge backup    - Execute state capture, hot SQLite dumps, host configs, encryption, and upload
  kedge restore   - Decrypt and restore state to host or isolated directory
  kedge verify    - Read-only database integrity checks and key verification
  kedge schedule  - Manage automated crontab execution with >= 1 hour rate guard
  kedge komodo    - Komodo Periphery GitOps manifest management
"""

import argparse
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Dict, List, Optional

from kedge import __version__
from kedge.config import ConfigManager
from kedge.core.deps import DependencyManager
from kedge.core.models import DiscoveredTopology, StackInfo
from kedge.core.runner import Runner
from kedge.core.ui import UI
from kedge.crypto.gpg import CryptoEngine
from kedge.discovery.docker_sniff import DockerSniffer
from kedge.discovery.fs_sniff import FSSniffer
from kedge.discovery.host_sniff import HostSniffer
from kedge.discovery.sqlite_sniff import SQLiteSniffer
from kedge.engine.backup_engine import BackupEngine
from kedge.engine.restore_engine import RestoreEngine
from kedge.engine.sqlite_engine import SQLiteEngine
from kedge.gitops.komodo import KomodoIntegration
from kedge.gitops.submodules import SubmoduleOrchestrator
from kedge.scheduler.cron_guard import CronGuard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kedge",
        description="Autonomous GitOps & Resilient State Fabric Engine for Containers and Host State",
    )
    parser.add_argument("--version", action="version", version=f"kedge {__version__}")
    parser.add_argument("--config", "-c", help="Path to custom .kedge.yaml or .kedge.json config file")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # init
    p_init = subparsers.add_parser("init", help="Run system onboarding & configuration wizard")
    p_init.add_argument("--non-interactive", action="store_true", help="Auto-detect and write defaults without prompts")
    p_init.add_argument("--workspace", help="Orchestrator workspace directory")
    p_init.add_argument("--remote", help="Remote Git repository URL for the orchestrator (e.g. git@github.com:user/orchestrator.git)")
    p_init.add_argument("--storage", choices=["local", "github", "s3"], default="local", help="Storage backend")
    p_init.add_argument("--repo", help="GitHub repository (for github backend, e.g. user/orchestrator)")
    p_init.add_argument("--auto-install-deps", action="store_true", help="Automatically install missing dependencies via package manager")

    # discover / scan
    for cmd_name in ["discover", "scan"]:
        p_disc = subparsers.add_parser(cmd_name, help="Scan host, containers, volumes, SQLite DBs, and OS configs")
        p_disc.add_argument("--json", action="store_true", help="Output discovered inventory as JSON")
        p_disc.add_argument("--save", action="store_true", help="Automatically save discovered paths into .kedge.yaml")

    # add
    p_add = subparsers.add_parser("add", help="Add a container stack, data path, or host config to tracking")
    p_add.add_argument("target", help="Path or name to track (e.g. /opt/docker/data/app or my-stack)")
    p_add.add_argument("--type", choices=["stack", "data", "host"], default="data", help="Type of target")

    # drop
    p_drop = subparsers.add_parser("drop", help="Remove an item from tracking")
    p_drop.add_argument("target", help="Path or name to remove")

    # status
    p_status = subparsers.add_parser("status", help="Show system status, tracked items, and remote backups")

    # config
    p_cfg = subparsers.add_parser("config", help="View or modify Kedge configuration settings")
    p_cfg.add_argument("action", choices=["show", "list", "get", "set", "reset"], help="Config action")
    p_cfg.add_argument("key", nargs="?", help="Configuration key (e.g. remote, schedule, retention, workspace_root)")
    p_cfg.add_argument("value", nargs="?", help="Configuration value to set")

    # backup
    p_backup = subparsers.add_parser("backup", help="Execute disaster recovery backup cycle")
    p_backup.add_argument("--dry-run", action="store_true", help="Perform discovery and staging checks without uploading")
    p_backup.add_argument("--skip-git", action="store_true", help="Skip Git submodule synchronization")
    p_backup.add_argument("--skip-host", action="store_true", help="Skip host OS configurations capture")

    # restore
    p_restore = subparsers.add_parser("restore", help="Decrypt and restore state from backup")
    p_restore.add_argument("--tag", help="Specific release tag to restore (defaults to latest)")
    p_restore.add_argument("--target-dir", help="Directory to restore into (sandbox for testing or alternate root)")
    p_restore.add_argument("--dry-run", action="store_true", help="Preview restore contents without overwriting files")
    p_restore.add_argument("--launch", action="store_true", help="Automatically run `docker compose up -d` after restore")

    # verify
    p_verify = subparsers.add_parser("verify", help="Read-only integrity checks on live databases and decryption keys")

    # schedule
    p_sched = subparsers.add_parser("schedule", help="Manage automated cron schedule")
    p_sched.add_argument("action", choices=["install", "remove", "status"], help="Schedule action")
    p_sched.add_argument("--cron", default="0 3 * * *", help="Cron expression (enforced >= 1 hour interval)")

    # komodo
    p_komodo = subparsers.add_parser("komodo", help="Komodo GitOps periphery management")
    p_komodo.add_argument("action", choices=["init"], help="Komodo action")
    p_komodo.add_argument("--port", type=int, default=8120, help="Periphery listening port")

    return parser


def cmd_discover(cfg_mgr: ConfigManager, as_json: bool = False, save: bool = False) -> int:
    UI.step("Initiating Autonomous Topology & Host Discovery")

    # 1. Docker stacks & mounts
    docker_stacks, volumes, docker_warns = DockerSniffer.sniff()
    for w in docker_warns:
        UI.warn(w)

    # 2. Filesystem fallback if needed
    fs_stacks = {}
    if not docker_stacks:
        UI.info("Docker daemon inspection yielded no stacks; falling back to filesystem traversal...")
        fs_stacks = FSSniffer.scan_directories()

    all_stacks = {**fs_stacks, **docker_stacks}

    # 3. Data paths resolution (excluding bulk media/storage pools)
    candidate_data_paths: set = set()
    skipped_bulk_media: list = []
    for v in volumes:
        if v.is_bulk_media:
            skipped_bulk_media.append(v.source)
        else:
            candidate_data_paths.add(v.source)

    workspace_root = cfg_mgr.data.get("workspace_root")
    if workspace_root and (Path(workspace_root) / "data").is_dir():
        candidate_data_paths.add(str(Path(workspace_root) / "data"))

    if skipped_bulk_media:
        UI.info(f"Filtered {len(skipped_bulk_media)} bulk media/storage mount(s) from state backup (movies, camera, downloads, etc.).")

    # 4. SQLite magic-byte scan
    discovered_dbs = []
    for dp in candidate_data_paths:
        discovered_dbs.extend(SQLiteSniffer.scan_path(dp, base_root=workspace_root))

    # 5. Host configurations
    host_items = HostSniffer.sniff_all(
        categories=cfg_mgr.data.get("tracked_host_items"),
        custom_paths=cfg_mgr.data.get("custom_host_paths"),
    )

    topology = DiscoveredTopology(
        stacks=all_stacks,
        volumes=volumes,
        databases=discovered_dbs,
        host_configs=host_items,
        warnings=docker_warns,
    )

    if as_json:
        import json
        print(json.dumps(topology.to_dict(), indent=2))
        return 0

    # Display Tables
    UI.step(f"Discovered Stacks ({len(all_stacks)})")
    stack_rows = []
    for s_path, s_info in all_stacks.items():
        git_badge = f"{UI.GREEN}[Git]{UI.RESET}" if s_info.is_git_repo else f"{UI.GRAY}[No Git]{UI.RESET}"
        containers_str = ", ".join(s_info.container_names[:3])
        if len(s_info.container_names) > 3:
            containers_str += f" (+{len(s_info.container_names) - 3})"
        stack_rows.append([s_info.name, s_info.workdir, git_badge, containers_str])
    UI.table(["Stack Name", "Working Directory", "Version Control", "Containers"], stack_rows)

    UI.step(f"Discovered Persistent Read-Write Mounts ({len(volumes)})")
    vol_rows = []
    for v in volumes[:25]:
        status = f"{UI.GRAY}Bulk Media (Ignored){UI.RESET}" if v.is_bulk_media else f"{UI.GREEN}State Volume{UI.RESET}"
        vol_rows.append([v.container_name, v.source, v.destination, status])
    UI.table(["Container", "Host Mount Path", "Container Destination", "Classification"], vol_rows)

    UI.step(f"Discovered SQLite Databases ({len(discovered_dbs)})")
    db_rows = []
    for d in discovered_dbs:
        wal_badge = f"{UI.YELLOW}Active WAL{UI.RESET}" if d.has_wal else f"{UI.GRAY}Standard{UI.RESET}"
        db_rows.append([d.relative_path, UI.format_bytes(d.size_bytes), wal_badge, d.path])
    UI.table(["Relative Path", "File Size", "Mode", "Host Path"], db_rows)

    UI.step(f"Discovered Host OS State & Configurations ({len(host_items)})")
    host_rows = []
    for h in host_items[:15]:
        host_rows.append([h.category.upper(), h.source_path, h.relative_path])
    UI.table(["Category", "Host Path", "Backup Target"], host_rows)

    if save:
        cfg_mgr.data["data_paths"] = list(candidate_data_paths)
        cfg_mgr.data["tracked_stacks"] = [s.name for s in all_stacks.values()]
        cfg_mgr.save()
        UI.success(f"Saved discovered paths into {cfg_mgr.config_path}")

    return 0


def cmd_add(cfg_mgr: ConfigManager, target: str, target_type: str) -> int:
    resolved_path = str(Path(target).expanduser().resolve())
    if target_type == "data":
        data_paths = cfg_mgr.data.get("data_paths", [])
        if resolved_path not in data_paths:
            data_paths.append(resolved_path)
            cfg_mgr.data["data_paths"] = data_paths
            cfg_mgr.save()
            UI.success(f"Added data path to tracking: {resolved_path}")
        else:
            UI.info(f"Path already tracked: {resolved_path}")

    elif target_type == "stack":
        stacks = cfg_mgr.data.get("tracked_stacks", [])
        if target not in stacks:
            stacks.append(target)
            cfg_mgr.data["tracked_stacks"] = stacks
            cfg_mgr.save()
            UI.success(f"Added stack to tracking: {target}")
        else:
            UI.info(f"Stack already tracked: {target}")

    elif target_type == "host":
        custom_paths = cfg_mgr.data.get("custom_host_paths", [])
        if resolved_path not in custom_paths:
            custom_paths.append(resolved_path)
            cfg_mgr.data["custom_host_paths"] = custom_paths
            cfg_mgr.save()
            UI.success(f"Added host path to tracking: {resolved_path}")
        else:
            UI.info(f"Host path already tracked: {resolved_path}")

    return 0


def cmd_drop(cfg_mgr: ConfigManager, target: str) -> int:
    resolved_path = str(Path(target).expanduser().resolve())
    modified = False

    data_paths = cfg_mgr.data.get("data_paths", [])
    if resolved_path in data_paths:
        data_paths.remove(resolved_path)
        cfg_mgr.data["data_paths"] = data_paths
        modified = True
        UI.success(f"Removed data path: {resolved_path}")

    stacks = cfg_mgr.data.get("tracked_stacks", [])
    if target in stacks:
        stacks.remove(target)
        cfg_mgr.data["tracked_stacks"] = stacks
        modified = True
        UI.success(f"Removed stack: {target}")

    custom_paths = cfg_mgr.data.get("custom_host_paths", [])
    if resolved_path in custom_paths:
        custom_paths.remove(resolved_path)
        cfg_mgr.data["custom_host_paths"] = custom_paths
        modified = True
        UI.success(f"Removed custom host path: {resolved_path}")

    if modified:
        cfg_mgr.save()
    else:
        UI.warn(f"Target '{target}' not found in active tracking lists.")

    return 0


def cmd_status(cfg_mgr: ConfigManager) -> int:
    UI.step("Kedge State Fabric Status")
    UI.bullet("Configuration File", str(cfg_mgr.config_path))
    UI.bullet("Workspace Root", cfg_mgr.data.get("workspace_root") or "(Unset - using cwd)")
    UI.bullet("Storage Backend", cfg_mgr.data.get("storage", {}).get("backend", "local"))

    remote = cfg_mgr.get_value("remote")
    if remote:
        UI.bullet("Remote Git Origin", remote)

    data_paths = cfg_mgr.data.get("data_paths", [])
    UI.bullet("Tracked Data Paths", f"{len(data_paths)} path(s)")
    for dp in data_paths:
        p = Path(dp)
        size_str = UI.format_bytes(sum(f.stat().st_size for f in p.glob("**/*") if f.is_file())) if p.is_dir() else "N/A"
        print(f"    • {dp} ({size_str})")

    tracked_stacks = cfg_mgr.data.get("tracked_stacks", [])
    UI.bullet("Tracked Stacks", ", ".join(tracked_stacks) if tracked_stacks else "(None)")

    tracked_host = cfg_mgr.data.get("tracked_host_items", [])
    UI.bullet("Tracked Host Categories", ", ".join(tracked_host))

    # Encryption Key
    key_file_str = cfg_mgr.data.get("encryption", {}).get("key_file", "~/.config/kedge/kedge.key")
    key_file = Path(key_file_str).expanduser()
    key_status = f"{UI.GREEN}Present{UI.RESET} ({key_file})" if key_file.is_file() else f"{UI.RED}Missing{UI.RESET}"
    UI.bullet("AES-256 Key Status", key_status)

    # Remote Release Status
    storage_cfg = cfg_mgr.data.get("storage", {})
    backend = storage_cfg.get("backend", "local")
    if backend == "github" and storage_cfg.get("github_repo"):
        from kedge.storage.github_release import GitHubReleaseProvider
        gh_prov = GitHubReleaseProvider(repo=storage_cfg["github_repo"])
        backups = gh_prov.list_backups()
        UI.bullet("GitHub Releases", f"{len(backups)} snapshot(s) found in {storage_cfg['github_repo']}")
        if backups:
            for b in backups[:3]:
                print(f"    • {b.get('tag')} - {b.get('title')} ({b.get('created_at', '')[:10]})")
    elif backend == "local":
        from kedge.storage.local import LocalStorageProvider
        loc_prov = LocalStorageProvider(target_dir=storage_cfg.get("local_path", "/var/backups/kedge"))
        backups = loc_prov.list_backups()
        UI.bullet("Local Backups", f"{len(backups)} snapshot(s) found in {loc_prov.target_dir}")

    # Schedule
    cron_expr = cfg_mgr.get_value("schedule.cron") or "(Disabled)"
    UI.bullet("Schedule Interval", cron_expr)

    # Komodo Periphery
    komodo_enabled = cfg_mgr.data.get("gitops", {}).get("komodo_periphery", False)
    UI.bullet("Komodo Inbound GitOps", f"{UI.GREEN}Enabled{UI.RESET} (Port {cfg_mgr.data.get('gitops', {}).get('periphery_port', 8120)})" if komodo_enabled else f"{UI.GRAY}Disabled{UI.RESET}")

    return 0


def cmd_config(cfg_mgr: ConfigManager, action: str, key: Optional[str] = None, value: Optional[str] = None) -> int:
    if action in ("show", "list"):
        UI.step("Kedge Configuration Settings")
        flat = cfg_mgr.get_flattened()
        rows = [[k, str(v)] for k, v in sorted(flat.items())]
        UI.table(["Configuration Key", "Current Value"], rows)
        return 0

    elif action == "get":
        if not key:
            UI.error("Usage: kedge config get <key>")
            return 1
        val = cfg_mgr.get_value(key)
        if val is None:
            UI.error(f"Configuration key '{key}' not found.")
            return 1
        print(val)
        return 0

    elif action == "set":
        if not key or value is None:
            UI.error("Usage: kedge config set <key> <value>")
            return 1
        ok = cfg_mgr.set_value(key, value)
        if ok:
            UI.success(f"Updated '{key}' = '{value}' in {cfg_mgr.config_path}")
            # If schedule was updated, check crontab update
            if key in ("schedule", "cron", "scheduler.cron_expression"):
                if value.lower() in ("false", "off", "disabled"):
                    CronGuard.remove()
                    UI.info("Crontab schedule entry removed.")
                else:
                    self_bin = shutil.which("kedge") or str(Path(sys.argv[0]).resolve())
                    c_ok, c_msg = CronGuard.install(value, self_bin)
                    if c_ok:
                        UI.success(c_msg)
            return 0
        UI.error(f"Failed to update key '{key}'.")
        return 1

    elif action == "reset":
        cfg_mgr.reset()
        UI.success(f"Configuration reset to defaults in {cfg_mgr.config_path}")
        return 0

    return 0


def cmd_verify(cfg_mgr: ConfigManager) -> int:
    UI.step("Running Read-Only Database & Key Integrity Verification")

    # 1. Check Key
    key_file_str = cfg_mgr.data.get("encryption", {}).get("key_file", "~/.config/kedge/kedge.key")
    key_file = Path(key_file_str).expanduser()
    if not key_file.is_file():
        UI.error(f"Encryption key file not found: {key_file}")
    else:
        UI.success(f"AES-256 Key file verified: {key_file}")

    # 2. Check Databases
    workspace_root = cfg_mgr.data.get("workspace_root")
    data_paths = [Path(p) for p in cfg_mgr.data.get("data_paths", []) if Path(p).is_dir()]
    databases = []
    for dp in data_paths:
        databases.extend(SQLiteSniffer.scan_path(str(dp), base_root=workspace_root))

    if not databases:
        UI.info("No active SQLite databases found in tracked data paths.")
        return 0

    all_passed = True
    for db in databases:
        ok, msg = SQLiteEngine.check_integrity(Path(db.path))
        if ok:
            UI.success(f"[OK] {db.relative_path} (PRAGMA integrity_check passed)")
        else:
            UI.error(f"[FAIL] {db.relative_path}: {msg}")
            all_passed = False

    if all_passed:
        UI.success(f"All {len(databases)} database(s) passed live integrity validation.")
        return 0
    return 1


def cmd_init(
    cfg_mgr: ConfigManager,
    non_interactive: bool = False,
    workspace: Optional[str] = None,
    remote: Optional[str] = None,
    storage: str = "local",
    repo: Optional[str] = None,
    auto_install_deps: bool = False,
) -> int:
    UI.banner()
    UI.step("Starting Kedge System Audit & Unified Onboarding Wizard")

    # Step 0: Dependency Verification & Permission Prompt
    UI.step("Step 0: System Toolchain & Dependency Verification")
    deps_ok = DependencyManager.ensure_dependencies(
        auto_install=auto_install_deps,
        prompt_user=not non_interactive,
    )
    if not deps_ok:
        UI.warn("Proceeding with partial dependencies; some functions may be unavailable.")
    else:
        UI.success("System toolchain verified.")

    # Step 1: Workspace Root
    UI.step("Step 1: Orchestrator Workspace Root")
    detected_root = os.getcwd()
    for cand in ["/opt/docker", "/srv/docker", "/var/lib/docker/compose", "/Docker"]:
        if Path(cand).is_dir():
            detected_root = cand
            break
    target_root_str = workspace or cfg_mgr.data.get("workspace_root") or detected_root

    if not non_interactive and sys.stdin.isatty():
        try:
            inp = input(f"Set orchestrator parent workspace directory [{target_root_str}]: ").strip()
            if inp:
                target_root_str = inp
        except (KeyboardInterrupt, EOFError):
            print()

    ws = Path(target_root_str).expanduser().resolve()
    ws.mkdir(parents=True, exist_ok=True)
    cfg_mgr.data["workspace_root"] = str(ws)
    UI.success(f"Workspace set to: {ws}")

    # Step 2: Remote Orchestrator Git Strategy (Inbound GitOps & Outbound Release Target)
    UI.step("Step 2: Remote Orchestrator Git Strategy (Origin & Artifacts)")
    curr_remote = remote or cfg_mgr.data.get("gitops", {}).get("remote_url", "")
    if not curr_remote and Runner.has_command("git"):
        # Check if git remote already exists in ws
        rem_res = Runner.run(["git", "remote", "get-url", "origin"], cwd=str(ws))
        if rem_res.ok and rem_res.stdout.strip():
            curr_remote = rem_res.stdout.strip()

    if not non_interactive and sys.stdin.isatty():
        prompt_msg = f"Set remote Git repository URL (e.g. git@github.com:user/orchestrator.git) [{curr_remote or 'skip'}]: "
        try:
            inp = input(prompt_msg).strip()
            if inp:
                curr_remote = inp
        except (KeyboardInterrupt, EOFError):
            print()

    if curr_remote and curr_remote != "skip":
        cfg_mgr.data.setdefault("gitops", {})["remote_url"] = curr_remote
        # Initialize Git repo if needed
        SubmoduleOrchestrator.init_parent_repo(ws)

        # Set or update origin
        rem_check = Runner.run(["git", "remote", "get-url", "origin"], cwd=str(ws))
        if rem_check.ok:
            Runner.run(["git", "remote", "set-url", "origin", curr_remote], cwd=str(ws))
        else:
            Runner.run(["git", "remote", "add", "origin", curr_remote], cwd=str(ws))

        UI.success(f"Configured Git remote origin: {curr_remote}")

        # If it's a GitHub repo, automatically link storage backend to GitHub Releases!
        if "github.com" in curr_remote:
            gh_repo_part = curr_remote.split("github.com", 1)[1].lstrip("/:").removesuffix(".git")
            cfg_mgr.data["storage"]["backend"] = "github"
            cfg_mgr.data["storage"]["github_repo"] = gh_repo_part
            UI.success(f"Configured GitHub Releases artifact storage in: {gh_repo_part}")
        else:
            cfg_mgr.data["storage"]["backend"] = storage
    else:
        cfg_mgr.data["storage"]["backend"] = storage
        if repo:
            cfg_mgr.data["storage"]["github_repo"] = repo

    # Step 3: Auto-Discover Stacks & Storage Volumes
    UI.step("Step 3: Auto-Discovering Stacks & Storage Volumes")
    docker_stacks, volumes, _ = DockerSniffer.sniff()
    discovered_stacks = list(docker_stacks.values())

    candidate_data: List[str] = []
    skipped_media = 0
    for v in volumes:
        if v.is_bulk_media:
            skipped_media += 1
            continue
        if v.source not in candidate_data:
            candidate_data.append(v.source)

    if (ws / "data").is_dir() and str(ws / "data") not in candidate_data:
        candidate_data.append(str(ws / "data"))

    cfg_mgr.data["data_paths"] = candidate_data
    cfg_mgr.data["tracked_stacks"] = [s.name for s in discovered_stacks]

    media_note = f" (filtered {skipped_media} bulk media mounts)" if skipped_media > 0 else ""
    UI.info(f"Discovered {len(discovered_stacks)} active Compose stacks and {len(candidate_data)} state data paths{media_note}.")

    # Review Discovered Modules
    if discovered_stacks and not non_interactive and sys.stdin.isatty():
        print(f"\n{UI.BOLD}Discovered Container Stacks:{UI.RESET}")
        for s in discovered_stacks:
            print(f"  • {UI.GREEN}{s.name}{UI.RESET} ({s.workdir})")

        try:
            confirm = input(f"Track these {len(discovered_stacks)} stacks under Kedge GitOps? [Y/n]: ").strip().lower()
            if confirm in ("n", "no"):
                cfg_mgr.data["tracked_stacks"] = []
        except (KeyboardInterrupt, EOFError):
            print()

    # Step 4: Cryptographic Key Setup
    UI.step("Step 4: Cryptographic Key Setup (AES-256)")
    default_key = cfg_mgr.data.get("encryption", {}).get("key_file", "~/.config/kedge/kedge.key")
    key_file_input = default_key
    if not non_interactive and sys.stdin.isatty():
        try:
            inp = input(f"Passphrase/key location [{default_key}]: ").strip()
            if inp:
                key_file_input = inp
        except (KeyboardInterrupt, EOFError):
            print()

    cfg_mgr.data.setdefault("encryption", {})["key_file"] = key_file_input
    key_path = CryptoEngine.ensure_key_file(key_file_input)
    UI.success(f"AES-256 encryption key active at: {key_path}")

    # Step 5: Unified Inbound GitOps (Komodo Integration)
    UI.step("Step 5: Unified Inbound GitOps (Komodo Periphery Agent)")
    deploy_komodo = True
    if not non_interactive and sys.stdin.isatty():
        try:
            inp = input(f"Deploy local Komodo Periphery agent to enable automated Git pull-on-push webhooks? [Y/n]: ").strip().lower()
            if inp in ("n", "no"):
                deploy_komodo = False
        except (KeyboardInterrupt, EOFError):
            print()

    if deploy_komodo:
        komodo_dir = ws / "containers" / "komodo-periphery"
        port = cfg_mgr.data.get("gitops", {}).get("periphery_port", 8120)
        ok, k_path = KomodoIntegration.generate_manifest(komodo_dir, port=port)
        cfg_mgr.data.setdefault("gitops", {})["komodo_periphery"] = True
        UI.success(f"Komodo Periphery manifest generated at: {k_path}")
        UI.info(f"Connect your Komodo Core dashboard to port {port} on this host to enable webhooks.")

    # Step 6: Automated Backup Schedule Setup
    UI.step("Step 6: Automated Schedule Setup (Safe Rate-Limited Interval)")
    cron_expr = "0 3 * * *"
    if not non_interactive and sys.stdin.isatty():
        print("Choose an automated backup frequency (Minimum permitted: 1 Hour):")
        print("  1) Daily at 03:00 AM (Recommended)")
        print("  2) Twice daily (03:00 AM, 03:00 PM)")
        print("  3) Hourly (Minimum interval)")
        print("  4) Custom (Enforced >= 60 minutes)")
        print("  5) Disable automated scheduling")
        try:
            choice = input("Option [1-5]: ").strip()
            if choice == "2":
                cron_expr = "0 3,15 * * *"
            elif choice == "3":
                cron_expr = "0 * * * *"
            elif choice == "4":
                custom = input("Enter custom cron expression: ").strip()
                valid, msg = CronGuard.validate_expression(custom)
                if valid:
                    cron_expr = custom
                else:
                    UI.warn(f"{msg}. Forcing daily at 03:00 AM.")
            elif choice == "5":
                cron_expr = ""
        except (KeyboardInterrupt, EOFError):
            print()

    if cron_expr:
        cfg_mgr.data.setdefault("scheduler", {})["enabled"] = True
        cfg_mgr.data["scheduler"]["cron_expression"] = cron_expr
        self_bin = shutil.which("kedge") or str(Path(sys.argv[0]).resolve())
        c_ok, c_msg = CronGuard.install(cron_expr, self_bin)
        if c_ok:
            UI.success(c_msg)
    else:
        cfg_mgr.data.setdefault("scheduler", {})["enabled"] = False
        CronGuard.remove()

    # Step 7: Persist Configuration
    UI.step("Step 7: Writing Configuration")
    cfg_path = cfg_mgr.save()
    UI.success(f"Configuration saved to: {cfg_path}")

    # Step 8: Initialize & Publish Declarative Infrastructure to Remote Git Origin
    if curr_remote and curr_remote != "skip":
        UI.step("Step 8: Publishing Declarative Infrastructure to Remote Git Origin")
        p_ok, p_msg = SubmoduleOrchestrator.ensure_remote_initialized(ws, curr_remote)
        if p_ok:
            UI.success(p_msg)
        else:
            UI.warn(p_msg)

    print(f"\n{UI.GREEN}{UI.BOLD}Kedge Onboarding Complete.{UI.RESET}")
    print(f"Run {UI.CYAN}{UI.BOLD}kedge backup{UI.RESET} to perform an initial snapshot.")
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    cfg_mgr = ConfigManager(explicit_path=args.config)

    if args.command == "init":
        sys.exit(
            cmd_init(
                cfg_mgr,
                non_interactive=args.non_interactive,
                workspace=args.workspace,
                remote=args.remote,
                storage=args.storage,
                repo=args.repo,
                auto_install_deps=args.auto_install_deps,
            )
        )

    elif args.command in ("discover", "scan"):
        sys.exit(cmd_discover(cfg_mgr, as_json=args.json, save=args.save))

    elif args.command == "add":
        sys.exit(cmd_add(cfg_mgr, target=args.target, target_type=args.type))

    elif args.command == "drop":
        sys.exit(cmd_drop(cfg_mgr, target=args.target))

    elif args.command == "status":
        sys.exit(cmd_status(cfg_mgr))

    elif args.command == "config":
        sys.exit(cmd_config(cfg_mgr, action=args.action, key=args.key, value=args.value))

    elif args.command == "verify":
        sys.exit(cmd_verify(cfg_mgr))

    elif args.command == "backup":
        engine = BackupEngine(cfg_mgr)
        ok, msg = engine.run(dry_run=args.dry_run, skip_git=args.skip_git, skip_host=args.skip_host)
        sys.exit(0 if ok else 1)

    elif args.command == "restore":
        engine = RestoreEngine(cfg_mgr)
        ok, msg = engine.run(tag=args.tag, target_dir=args.target_dir, dry_run=args.dry_run, launch_containers=args.launch)
        sys.exit(0 if ok else 1)

    elif args.command == "schedule":
        if args.action == "install":
            self_bin = shutil.which("kedge") or str(Path(sys.argv[0]).resolve())
            ok, msg = CronGuard.install(args.cron, self_bin)
            if ok:
                UI.success(msg)
                sys.exit(0)
            else:
                UI.error(msg)
                sys.exit(1)
        elif args.action == "remove":
            ok, msg = CronGuard.remove()
            UI.info(msg)
            sys.exit(0)
        elif args.action == "status":
            res = Runner.run(["crontab", "-l"])
            if res.ok and "kedge" in res.stdout:
                UI.success("Kedge schedule active in crontab:")
                for line in res.stdout.splitlines():
                    if "kedge" in line:
                        print(f"  {line}")
            else:
                UI.info("No active Kedge crontab schedule found.")
            sys.exit(0)

    elif args.command == "komodo":
        if args.action == "init":
            ws = Path(cfg_mgr.data.get("workspace_root", os.getcwd()))
            dest = ws / "containers" / "komodo-periphery"
            ok, p = KomodoIntegration.generate_manifest(dest, port=args.port)
            UI.success(f"Generated Komodo Periphery manifest at: {p}")
            sys.exit(0)


if __name__ == "__main__":
    main()
