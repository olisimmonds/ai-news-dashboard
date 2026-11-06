"""Simple local accounts and signed-cookie sessions for the private browser."""

from __future__ import annotations

import argparse
import base64
import contextlib
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

from ai_dashboard.config import Settings

logger = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


class BrowserAuthError(ValueError):
    pass


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def validate_username(username: str) -> str:
    if not USERNAME_PATTERN.fullmatch(username):
        raise BrowserAuthError(
            "Usernames must be 1-64 characters using letters, numbers, dot, dash or underscore."
        )
    return username


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if len(password) < 8:
        raise BrowserAuthError("Passwords must be at least 8 characters.")
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$", 5)
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_b64decode(expected)),
        )
        return hmac.compare_digest(derived, _b64decode(expected))
    except (ValueError, TypeError, MemoryError):
        return False


def load_accounts(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read browser accounts from %s: %s", path, exc)
        return {}

    users = payload.get("users") if isinstance(payload, dict) else None
    if not isinstance(users, dict):
        return {}

    accounts: dict[str, str] = {}
    for username, entry in users.items():
        if not isinstance(username, str) or not isinstance(entry, dict):
            continue
        password_hash = entry.get("password_hash")
        if isinstance(password_hash, str):
            accounts[username] = password_hash
    return accounts


def save_accounts(path: Path, accounts: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "users": {name: {"password_hash": value} for name, value in sorted(accounts.items())}
    }
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(temporary, 0o600)
    temporary.replace(path)


class BrowserAuth:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def accounts_file(self) -> Path:
        return self._settings.browser_users_file

    def accounts_configured(self) -> bool:
        return bool(load_accounts(self.accounts_file))

    def user_exists(self, username: str) -> bool:
        return username in load_accounts(self.accounts_file)

    def authenticate(self, username: str, password: str) -> bool:
        accounts = load_accounts(self.accounts_file)
        encoded = accounts.get(username)
        return bool(encoded and verify_password(password, encoded))

    def issue_token(self, username: str) -> tuple[str, int]:
        expires = int(time.time()) + self._settings.browser_session_ttl_hours * 3600
        payload = _b64encode(
            json.dumps(
                {"sub": username, "exp": expires, "iat": int(time.time())},
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = _b64encode(
            hmac.new(self._secret(), payload.encode("ascii"), hashlib.sha256).digest()
        )
        return f"{payload}.{signature}", expires

    def verify_token(self, token: str | None) -> str | None:
        if not token or "." not in token:
            return None
        payload, signature = token.rsplit(".", 1)
        expected = _b64encode(
            hmac.new(self._secret(), payload.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            data: Any = json.loads(_b64decode(payload))
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        username = data.get("sub")
        expires = data.get("exp")
        if not isinstance(username, str) or not isinstance(expires, int):
            return None
        if expires < int(time.time()):
            return None
        return username if self.user_exists(username) else None

    def _secret(self) -> bytes:
        configured = self._settings.browser_secret
        if configured:
            return configured.encode("utf-8")

        path = self._settings.database_path.parent / "browser_auth.secret"
        try:
            return path.read_bytes()
        except FileNotFoundError:
            pass

        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_urlsafe(48).encode("ascii")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return path.read_bytes()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
        return value


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise BrowserAuthError("Passwords do not match.")
    return password


def main(argv: list[str] | None = None) -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Manage Meridian private browser accounts.")
    parser.add_argument("--file", type=Path, default=settings.browser_users_file)
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set-user", help="Create or update a user")
    set_parser.add_argument("username")

    remove_parser = subparsers.add_parser("remove-user", help="Delete a user")
    remove_parser.add_argument("username")

    subparsers.add_parser("list-users", help="List usernames")

    args = parser.parse_args(argv)
    path: Path = args.file
    accounts = load_accounts(path)

    try:
        if args.command == "set-user":
            username = validate_username(args.username)
            accounts[username] = hash_password(_prompt_password())
            save_accounts(path, accounts)
            print(f"Saved browser account '{username}' to {path}")
        elif args.command == "remove-user":
            username = validate_username(args.username)
            if accounts.pop(username, None) is None:
                print(f"No such account: {username}", file=sys.stderr)
                raise SystemExit(1)
            save_accounts(path, accounts)
            print(f"Removed browser account '{username}'")
        else:
            for username in sorted(accounts):
                print(username)
    except BrowserAuthError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
