"""
Terminal UI and ANSI color formatting for Kedge.
Zero external dependencies.
"""

import sys
from typing import List, Tuple, Optional


class UI:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    ITALIC = "\033[3m"
    UNDERLINE = "\033[4m"

    # Colors
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GRAY = "\033[90m"

    # Backgrounds
    BG_BLUE = "\033[44m"
    BG_GRAY = "\033[100m"

    @classmethod
    def banner(cls) -> None:
        try:
            from kedge import __version__
            ver = __version__
        except Exception:
            ver = "1.0.1"

        art = f"""{cls.CYAN}{cls.BOLD}
   __  __          _            
  / / / /__   ____| | ____  ___ 
 / /_/ / _ \\ / __  |/ __ `/ _ \\
/ ,<  /  __// /_/ // /_/ /  __/
\\/|_| \\___/ \\__,_/ \\__, /\\___/ 
                  /____/        {cls.RESET}
{cls.BOLD}Autonomous GitOps & Resilient State Fabric Engine{cls.RESET}
{cls.GRAY}Version {ver} • No hardcoded paths • Multi-Cloud & Homelab{cls.RESET}
"""
        print(art)

    @classmethod
    def step(cls, title: str) -> None:
        print(f"\n{cls.BOLD}{cls.CYAN}==>{cls.RESET} {cls.BOLD}{title}{cls.RESET}")

    @classmethod
    def info(cls, msg: str) -> None:
        print(f"  {cls.GRAY}[i]{cls.RESET} {msg}")

    @classmethod
    def success(cls, msg: str) -> None:
        print(f"  {cls.GREEN}[✓]{cls.RESET} {cls.BOLD}{msg}{cls.RESET}")

    @classmethod
    def warn(cls, msg: str) -> None:
        print(f"  {cls.YELLOW}[!]{cls.RESET} {msg}")

    @classmethod
    def error(cls, msg: str) -> None:
        print(f"  {cls.RED}[✗]{cls.RESET} {cls.BOLD}{msg}{cls.RESET}", file=sys.stderr)

    @classmethod
    def bullet(cls, key: str, value: str = "") -> None:
        if value:
            print(f"  • {cls.BOLD}{key}:{cls.RESET} {value}")
        else:
            print(f"  • {key}")

    @classmethod
    def table(cls, headers: List[str], rows: List[List[str]]) -> None:
        if not rows:
            print(f"  {cls.GRAY}(No items){cls.RESET}")
            return

        col_widths = [len(h) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(col_widths):
                    col_widths[i] = max(col_widths[i], len(str(cell)))

        # Header
        header_str = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
        separator = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
        print(f"\n  {cls.BOLD}{header_str}{cls.RESET}")
        print(f"  {cls.GRAY}{separator}{cls.RESET}")

        # Rows
        for row in rows:
            row_str = " | ".join(f"{str(cell):<{col_widths[i]}}" for i, cell in enumerate(row))
            print(f"  {row_str}")
        print()

    @classmethod
    def format_bytes(cls, num_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if abs(num_bytes) < 1024.0:
                return f"{num_bytes:3.1f} {unit}"
            num_bytes /= 1024.0
        return f"{num_bytes:.1f} PB"
