"""
Safe subprocess execution and binary verification utilities.
"""

import shutil
import subprocess
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

        try:
            res = subprocess.run(
                target_cmd,
                cwd=cwd,
                env=env,
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
