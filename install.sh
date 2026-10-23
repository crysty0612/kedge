#!/usr/bin/env bash
# ==============================================================================
# Kedge Installer - Universal GitOps & Resilient State Fabric Appliance
# ==============================================================================
set -euo pipefail

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_CYAN="\033[36m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_RED="\033[31m"
C_MUTED="\033[90m"

echo -e "${C_CYAN}${C_BOLD}"
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│                    KEDGE INSTALLER                          │"
echo "│  Autonomous GitOps & Resilient State Fabric Engine          │"
echo "└─────────────────────────────────────────────────────────────┘"
echo -e "${C_RESET}"

# 1. Dependency Pre-Flight Check & Permission Prompt
echo -e "${C_BOLD}Checking system prerequisites...${C_RESET}"
MISSING_PKGS=()
REQUIRED_CMDS=("python3" "git" "sqlite3" "gpg" "tar")

for cmd in "${REQUIRED_CMDS[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo -e "  ${C_YELLOW}[!] Missing required tool:${C_RESET} ${C_BOLD}$cmd${C_RESET}"
        MISSING_PKGS+=("$cmd")
    else
        echo -e "  ${C_GREEN}[✓]${C_RESET} $cmd"
    fi
done

# Check optional tools
for opt_cmd in "docker" "gh"; do
    if ! command -v "$opt_cmd" >/dev/null 2>&1; then
        echo -e "  ${C_MUTED}[i] Optional tool not found:${C_RESET} $opt_cmd"
    else
        echo -e "  ${C_GREEN}[✓]${C_RESET} $opt_cmd"
    fi
done

# If packages missing, attempt auto-installation with user permission
if [ "${#MISSING_PKGS[@]}" -gt 0 ]; then
    echo -e "\n${C_YELLOW}${C_BOLD}Some required dependencies are missing.${C_RESET}"
    
    PKG_MGR=""
    for pm in "apt-get" "dnf" "pacman" "apk" "zypper"; do
        if command -v "$pm" >/dev/null 2>&1; then
            PKG_MGR="$pm"
            break
        fi
    done

    if [ -n "$PKG_MGR" ]; then
        INSTALL_CMD=""
        case "$PKG_MGR" in
            apt-get) INSTALL_CMD="sudo apt-get update && sudo apt-get install -y ${MISSING_PKGS[*]}" ;;
            dnf)     INSTALL_CMD="sudo dnf install -y ${MISSING_PKGS[*]}" ;;
            pacman)  INSTALL_CMD="sudo pacman -S --noconfirm ${MISSING_PKGS[*]}" ;;
            apk)     INSTALL_CMD="sudo apk add --no-cache ${MISSING_PKGS[*]}" ;;
            zypper)  INSTALL_CMD="sudo zypper install -y ${MISSING_PKGS[*]}" ;;
        esac

        echo -e "Kedge can install these packages using: ${C_BOLD}$INSTALL_CMD${C_RESET}"
        read -rp "Do you grant permission to run this installation command? [Y/n]: " grant_install
        if [[ ! "$grant_install" =~ ^[Nn]$ ]]; then
            echo -e "\nInstalling dependencies..."
            eval "$INSTALL_CMD"
        else
            echo -e "${C_RED}Installation aborted. Please install ${MISSING_PKGS[*]} manually.${C_RESET}"
            exit 1
        fi
    else
        echo -e "${C_RED}No supported package manager detected. Please install ${MISSING_PKGS[*]} manually.${C_RESET}"
        exit 1
    fi
fi

# Verify Python version
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo -e "\n${C_GREEN}[✓] Python ${PY_VER} verified.${C_RESET}"

# 2. Determine target directory
if [ -w "/usr/local/bin" ]; then
    TARGET_BIN="/usr/local/bin/kedge"
    LIB_DIR="/usr/local/lib/kedge"
elif [ -d "$HOME/.local/bin" ] || mkdir -p "$HOME/.local/bin"; then
    TARGET_BIN="$HOME/.local/bin/kedge"
    LIB_DIR="$HOME/.local/lib/kedge"
else
    TARGET_BIN="/tmp/kedge"
    LIB_DIR="/tmp/kedge_lib"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

write_kedge_wrapper() {
    local bin_dest="$1"
    local lib_dest="$2"
    mkdir -p "$(dirname "$bin_dest")" "$lib_dest"
    cp -r "$SCRIPT_DIR/kedge" "$lib_dest/"

    cat << EOF > "$bin_dest"
#!/usr/bin/env python3
import sys
from pathlib import Path

lib_path = "${lib_dest}"
if lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from kedge.cli import main

if __name__ == "__main__":
    main()
EOF
    chmod +x "$bin_dest" 2>/dev/null || true
}

echo -e "Installing Kedge to: ${C_BOLD}${TARGET_BIN}${C_RESET}..."
write_kedge_wrapper "$TARGET_BIN" "$LIB_DIR"

# If running under sudo, also update the invoking user's ~/.local/bin if present
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    USER_BIN="/home/${SUDO_USER}/.local/bin/kedge"
    USER_LIB="/home/${SUDO_USER}/.local/lib/kedge"
    if [ -d "/home/${SUDO_USER}/.local/bin" ] || [ -f "$USER_BIN" ]; then
        echo -e "Synchronizing user local path: ${C_BOLD}${USER_BIN}${C_RESET}..."
        write_kedge_wrapper "$USER_BIN" "$USER_LIB"
        chown -R "${SUDO_USER}:${SUDO_USER}" "$USER_BIN" "$USER_LIB" 2>/dev/null || true
    fi
fi

echo -e "\n${C_GREEN}${C_BOLD}[✓] Kedge successfully installed!${C_RESET}"
echo -e "To configure and onboard this machine, run: ${C_CYAN}${C_BOLD}kedge init${C_RESET}"
echo -e "To scan container topology and host configs: ${C_CYAN}${C_BOLD}kedge discover${C_RESET} (or ${C_CYAN}${C_BOLD}kedge scan${C_RESET})"
echo -e "To inspect and update settings: ${C_CYAN}${C_BOLD}kedge config show${C_RESET}"
