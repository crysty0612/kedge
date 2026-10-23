"""
Safe subprocess execution and binary verification utilities.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple, Union


class CommandResult:
    def __init__(self, command: str, returncode: int, stdout: str, stderr: str):
        self.command = command
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner:
    @staticmethod
    def which(binary: str) -> Optional[str]:
        return shutil.which(binary)

    @staticmethod
    def has_command(binary: str) -> bool:
        return shutil.which(binary) is not None

    @staticmethod
    def run(
        cmd: Union[str, List[str]],
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        timeout: Optional[int] = None,
        check: bool = False,
        shell: bool = False,
    ) -> CommandResult:
        if isinstance(cmd, list) and shell:
            cmd_str = " ".join(cmd)
        elif isinstance(cmd, str) and not shell:
            import shlex
            cmd_list = shlex.split(cmd)
        else:
            cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
            cmd_list = cmd if isinstance(cmd, list) else [cmd]

        target_cmd = cmd_str if shell else cmd_list

        run_env = env.copy() if env is not None else os.environ.copy()

        # Bridge user SSH and GitHub CLI credentials when running elevated under sudo
        if os.geteuid() == 0 and "SUDO_USER" in run_env:
            sudo_user = run_env["SUDO_USER"]
            user_home = Path(f"/home/{sudo_user}")
            if user_home.is_dir():
                if "GH_CONFIG_DIR" not in run_env:
                    u_gh = user_home / ".config" / "gh"
                    r_gh = Path("/root/.config/gh/hosts.yml")
                    if u_gh.is_dir() and not r_gh.is_file():
                        run_env["GH_CONFIG_DIR"] = str(u_gh)

                if "GIT_SSH_COMMAND" not in run_env:
                    u_ssh = user_home / ".ssh"
                    if u_ssh.is_dir():
                        ssh_keys: List[str] = []
                        for kname in ["id_ed25519", "id_rsa", "id_ecdsa"]:
                            kp = u_ssh / kname
                            if kp.is_file():
                                ssh_keys.append(f"-i {kp}")
                        ssh_cfg = u_ssh / "config"
                        cfg_part = f"-F {ssh_cfg} " if ssh_cfg.is_file() else ""
                        if ssh_keys:
                            run_env["GIT_SSH_COMMAND"] = f"ssh {cfg_part}{' '.join(ssh_keys)} -o StrictHostKeyChecking=accept-new"

        try:
            res = subprocess.run(
                target_cmd,
                cwd=cwd,
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                shell=shell,
            )
            result = CommandResult(
                command=str(cmd),
                returncode=res.returncode,
                stdout=res.stdout,
                stderr=res.stderr,
            )
            if check and not result.ok:
                raise subprocess.CalledProcessError(
                    returncode=res.returncode,
                    cmd=cmd,
                    output=res.stdout,
                    stderr=res.stderr,
                )
            return result
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                command=str(cmd),
                returncode=124,
                stdout=e.stdout or "" if isinstance(e.stdout, str) else "",
                stderr=f"Command timed out after {timeout} seconds",
            )
        except Exception as e:
            return CommandResult(
                command=str(cmd),
                returncode=1,
                stdout="",
                stderr=str(e),
            )
