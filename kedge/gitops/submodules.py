"""
Git submodule orchestration engine for Kedge.
Handles submodule tracking, automatic commits, synchronization, and bumps.
"""

import json
from pathlib import Path
from typing import List, Optional, Tuple

from kedge.core.models import StackInfo
from kedge.core.runner import Runner


class SubmoduleOrchestrator:
    @staticmethod
    def is_git_repo(path: Path) -> bool:
        return (path / ".git").exists()

    @classmethod
    def ensure_git_identity(cls, root_dir: Path) -> None:
        has_name = Runner.run(["git", "config", "user.name"], cwd=str(root_dir))
        has_email = Runner.run(["git", "config", "user.email"], cwd=str(root_dir))

        name = has_name.stdout.strip() if has_name.ok else ""
        email = has_email.stdout.strip() if has_email.ok else ""

        if not name or not email:
            gh_name = ""
            gh_email = ""
            if Runner.has_command("gh"):
                gh_user_res = Runner.run(["gh", "api", "user"], timeout=10)
                if gh_user_res.ok:
                    try:
                        u_data = json.loads(gh_user_res.stdout)
                        gh_name = u_data.get("name") or u_data.get("login") or ""
                        gh_email = u_data.get("email") or f"{u_data.get('login', 'kedge')}@users.noreply.github.com"
                    except Exception:
                        pass

            final_name = name or gh_name or "kedge-agent"
            final_email = email or gh_email or "kedge@localhost"

            Runner.run(["git", "config", "user.name", final_name], cwd=str(root_dir))
            Runner.run(["git", "config", "user.email", final_email], cwd=str(root_dir))

    @classmethod
    def init_parent_repo(cls, root_dir: Path) -> Tuple[bool, str]:
        if not cls.is_git_repo(root_dir):
            res = Runner.run(["git", "init", "-b", "main"], cwd=str(root_dir))
            if not res.ok:
                return False, f"Failed to initialize parent git repository: {res.stderr}"

        cls.ensure_git_identity(root_dir)

        # Ensure .gitignore exists and ignores state / secrets
        gitignore_path = root_dir / ".gitignore"
        if not gitignore_path.is_file():
            content = """# Kedge Generated Gitignore
# Persistent runtime state & secrets are captured exclusively via encrypted releases
/data/
**/data/
**/.env
**/*.env.*
**/*.pem
**/*.key
**/*.crt
**/*.sqlite*
**/*.db*
**/*.wal
**/*.shm
**/*.journal
**/*.log
**/*.bak*
**/*.tmp
"""
            gitignore_path.write_text(content, encoding="utf-8")
            Runner.run(["git", "add", ".gitignore"], cwd=str(root_dir))

        return True, "Parent repository initialized"

    @classmethod
    def ensure_remote_initialized(
        cls,
        root_dir: Path,
        remote_url: Optional[str] = None,
    ) -> Tuple[bool, str]:
        if not cls.is_git_repo(root_dir):
            cls.init_parent_repo(root_dir)

        cls.ensure_git_identity(root_dir)

        if remote_url:
            rem_check = Runner.run(["git", "remote", "get-url", "origin"], cwd=str(root_dir))
            if rem_check.ok:
                Runner.run(["git", "remote", "set-url", "origin", remote_url], cwd=str(root_dir))
            else:
                Runner.run(["git", "remote", "add", "origin", remote_url], cwd=str(root_dir))

        # Ensure README.md exists
        readme_path = root_dir / "README.md"
        if not readme_path.is_file():
            readme_content = """# Homelab GitOps Orchestrator (Kedge)

Automated GitOps & state fabric repository.
- Declarative Compose stacks are tracked in `containers/`.
- Runtime states, SQLite hot dumps, and encrypted volumes are published to GitHub Releases.
- Integrated with Komodo Periphery for inbound pull-on-push webhooks.
"""
            readme_path.write_text(readme_content, encoding="utf-8")

        # Stage declarative files
        Runner.run(["git", "add", ".gitignore", "README.md"], cwd=str(root_dir))

        containers_dir = root_dir / "containers"
        if containers_dir.is_dir():
            Runner.run(["git", "add", "containers/"], cwd=str(root_dir))

        for cfg_name in [".kedge.yaml", ".kedge.yml", ".kedge.json"]:
            if (root_dir / cfg_name).is_file():
                Runner.run(["git", "add", cfg_name], cwd=str(root_dir))

        # Check if HEAD commit exists
        head_check = Runner.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(root_dir))
        if not head_check.ok:
            c_res = Runner.run(
                ["git", "commit", "-m", "chore(kedge): initial declarative infrastructure commit"],
                cwd=str(root_dir),
            )
            if not c_res.ok:
                return False, f"Failed to create initial commit: {c_res.stderr}"

        # Check if origin is configured
        rem_url_res = Runner.run(["git", "remote", "get-url", "origin"], cwd=str(root_dir))
        if rem_url_res.ok and rem_url_res.stdout.strip():
            ls_res = Runner.run(["git", "ls-remote", "--heads", "origin"], cwd=str(root_dir), timeout=20)
            branch_res = Runner.run(["git", "branch", "--show-current"], cwd=str(root_dir))
            current_branch = branch_res.stdout.strip() or "main"

            if not ls_res.stdout.strip():
                # Remote repository is empty! Push initial branch
                push_res = Runner.run(["git", "push", "-u", "origin", f"HEAD:{current_branch}"], cwd=str(root_dir), timeout=60)
                if push_res.ok:
                    return True, f"Pushed initial branch '{current_branch}' with container stacks to remote origin."
                return False, f"Failed to push initial branch to remote: {push_res.stderr}"
            else:
                # Remote has branches, ensure latest commits are pushed
                push_res = Runner.run(["git", "push", "origin", "HEAD"], cwd=str(root_dir), timeout=60)
                if push_res.ok:
                    return True, "Synchronized latest commits to remote origin."

        return True, "Local GitOps orchestrator initialized."

    @classmethod
    def sync_submodules(cls, root_dir: Path, timestamp_str: str) -> Tuple[bool, List[str]]:
        logs: List[str] = []
        if not cls.is_git_repo(root_dir):
            return True, ["Parent directory is not a git repository. Skipping submodule sync."]

        # Ensure repo has an initial commit and remote is initialized
        ok, init_msg = cls.ensure_remote_initialized(root_dir)
        if ok and init_msg:
            logs.append(init_msg)

        # Check status and commit submodules
        sync_cmd = f"""git submodule foreach --recursive '
            if [ -n "$(git status --porcelain)" ]; then
                git add .
                git commit -m "chore(kedge): automated stack sync {timestamp_str}" || true
                git push origin HEAD || true
            fi
        '"""
        res = Runner.run(sync_cmd, cwd=str(root_dir), shell=True, timeout=120)
        if res.stdout.strip():
            logs.append(res.stdout.strip())

        # Bump submodules / stage declarative updates in parent repo
        Runner.run(["git", "add", ".gitmodules", "."], cwd=str(root_dir))
        diff_res = Runner.run(["git", "diff-index", "--quiet", "HEAD", "--"], cwd=str(root_dir))
        if not diff_res.ok:
            commit_res = Runner.run(
                ["git", "commit", "-m", f"chore(orchestrator): bump submodules to {timestamp_str}"],
                cwd=str(root_dir),
            )
            if commit_res.ok:
                logs.append("Committed updated configuration and submodule pointers in parent repository.")
                Runner.run(["git", "push", "origin", "HEAD"], cwd=str(root_dir))

        return True, logs

    @classmethod
    def add_submodule(cls, root_dir: Path, remote_url: str, rel_path: str) -> Tuple[bool, str]:
        if not cls.is_git_repo(root_dir):
            return False, f"Root {root_dir} is not a git repository."
        res = Runner.run(["git", "submodule", "add", remote_url, rel_path], cwd=str(root_dir))
        if res.ok:
            return True, f"Submodule added: {rel_path} -> {remote_url}"
        return False, f"Failed to add submodule: {res.stderr}"
