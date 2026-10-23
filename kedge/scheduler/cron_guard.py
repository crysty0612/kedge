"""
Automated scheduler manager with rate-limiting safety guardrails (>= 1 hour interval).
"""

from pathlib import Path
from typing import Optional, Tuple

from kedge.core.runner import Runner


class CronGuard:
    @staticmethod
    def validate_expression(expr: str) -> Tuple[bool, str]:
        parts = expr.strip().split()
        if len(parts) != 5:
            return False, "Invalid cron expression: must have exactly 5 fields (minute hour day month day-of-week)"

        min_field = parts[0]
        # Reject sub-hourly schedules: * or */N where N < 60
        if min_field == "*":
            return False, "Sub-hourly execution is prohibited to protect remote release quotas. Minimum permitted interval is 1 hour."

        if min_field.startswith("*/"):
            try:
                step = int(min_field[2:])
                if step < 60:
                    return False, f"Interval of {step} minutes is too frequent. Minimum permitted interval is 60 minutes."
            except ValueError:
                return False, f"Invalid minute field format: {min_field}"

        return True, "Valid cron expression"

    @classmethod
    def install(cls, expr: str, binary_path: str) -> Tuple[bool, str]:
        ok, msg = cls.validate_expression(expr)
        if not ok:
            return False, msg

        # Read existing crontab
        res = Runner.run(["crontab", "-l"])
        existing_lines = res.stdout.splitlines() if res.ok else []

        # Filter out old kedge entries
        new_lines = [line for line in existing_lines if "kedge backup" not in line]
        new_entry = f"{expr} {binary_path} backup > /var/log/kedge.log 2>&1"
        new_lines.append(new_entry)

        # Write back
        new_content = "\n".join(new_lines) + "\n"
        install_res = Runner.run(f"echo \"{new_content}\" | crontab -", shell=True)
        if install_res.ok:
            return True, f"Installed crontab schedule: {expr}"
        return False, f"Failed to install crontab: {install_res.stderr}"

    @classmethod
    def remove(cls) -> Tuple[bool, str]:
        res = Runner.run(["crontab", "-l"])
        if not res.ok:
            return True, "No crontab installed"

        lines = [line for line in res.stdout.splitlines() if "kedge backup" not in line]
        new_content = "\n".join(lines) + "\n"
        install_res = Runner.run(f"echo \"{new_content}\" | crontab -", shell=True)
        if install_res.ok:
            return True, "Removed kedge crontab entry"
        return False, f"Failed to update crontab: {install_res.stderr}"
