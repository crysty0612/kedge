"""
Cryptographic engine for Kedge: AES-256 symmetric encryption and decryption.
Supports GPG with automatic OpenSSL fallback.
"""

import os
from pathlib import Path
import secrets
from typing import Optional, Tuple

from kedge.core.runner import Runner


class CryptoEngine:
    @staticmethod
    def ensure_key_file(key_path_str: str) -> Path:
        key_path = Path(key_path_str).expanduser().resolve()
        if not key_path.is_file():
            key_path.parent.mkdir(parents=True, exist_ok=True)
            # Generate 32-byte cryptographically secure random passphrase
            random_key = secrets.token_urlsafe(32)
            key_path.write_text(random_key + "\n", encoding="utf-8")
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass

            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user and sudo_user != "root":
                try:
                    import pwd
                    pw = pwd.getpwnam(sudo_user)
                    if str(key_path).startswith(f"/home/{sudo_user}"):
                        os.chown(key_path, pw.pw_uid, pw.pw_gid)
                        if key_path.parent.exists():
                            os.chown(key_path.parent, pw.pw_uid, pw.pw_gid)
                except Exception:
                    pass
        return key_path

    @classmethod
    def encrypt(cls, src_file: Path, dest_file: Path, key_file: Path) -> Tuple[bool, str]:
        if not src_file.is_file():
            return False, f"Source file does not exist: {src_file}"
        if not key_file.is_file():
            return False, f"Key file does not exist: {key_file}"

        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if Runner.has_command("gpg"):
            cmd = [
                "gpg",
                "--batch",
                "--yes",
                "--passphrase-file",
                str(key_file),
                "--symmetric",
                "--cipher-algo",
                "AES256",
                "-o",
                str(dest_file),
                str(src_file),
            ]
            res = Runner.run(cmd, timeout=300)
            if res.ok and dest_file.is_file():
                return True, "Encryption succeeded via GPG AES256"
            return False, f"GPG encryption failed: {res.stderr}"

        elif Runner.has_command("openssl"):
            cmd = [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-pbkdf2",
                "-salt",
                "-in",
                str(src_file),
                "-out",
                str(dest_file),
                "-pass",
                f"file:{key_file}",
            ]
            res = Runner.run(cmd, timeout=300)
            if res.ok and dest_file.is_file():
                return True, "Encryption succeeded via OpenSSL AES-256-CBC"
            return False, f"OpenSSL encryption failed: {res.stderr}"

        return False, "Neither gpg nor openssl binary is available on this system"

    @classmethod
    def decrypt(cls, src_file: Path, dest_file: Path, key_file: Path) -> Tuple[bool, str]:
        if not src_file.is_file():
            return False, f"Encrypted archive does not exist: {src_file}"
        if not key_file.is_file():
            return False, f"Key file does not exist: {key_file}"

        dest_file.parent.mkdir(parents=True, exist_ok=True)

        if Runner.has_command("gpg"):
            cmd = [
                "gpg",
                "--batch",
                "--yes",
                "--passphrase-file",
                str(key_file),
                "--decrypt",
                "-o",
                str(dest_file),
                str(src_file),
            ]
            res = Runner.run(cmd, timeout=300)
            if res.ok and dest_file.is_file():
                return True, "Decryption succeeded via GPG"
            # If GPG fails, attempt OpenSSL in case archive was encrypted via OpenSSL
            if Runner.has_command("openssl"):
                cmd_ssl = [
                    "openssl",
                    "enc",
                    "-d",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-in",
                    str(src_file),
                    "-out",
                    str(dest_file),
                    "-pass",
                    f"file:{key_file}",
                ]
                res_ssl = Runner.run(cmd_ssl, timeout=300)
                if res_ssl.ok and dest_file.is_file():
                    return True, "Decryption succeeded via OpenSSL"
            return False, f"Decryption failed: {res.stderr}"

        elif Runner.has_command("openssl"):
            cmd = [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-in",
                str(src_file),
                "-out",
                str(dest_file),
                "-pass",
                f"file:{key_file}",
            ]
            res = Runner.run(cmd, timeout=300)
            if res.ok and dest_file.is_file():
                return True, "Decryption succeeded via OpenSSL"
            return False, f"OpenSSL decryption failed: {res.stderr}"

        return False, "Neither gpg nor openssl binary is available on this system"
