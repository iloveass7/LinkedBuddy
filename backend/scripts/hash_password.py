"""Print an argon2id hash of the owner password for OWNER_PASSWORD_HASH in .env.

Usage: uv run python scripts/hash_password.py
The password is read from a hidden prompt, never from argv, and is not echoed back.
"""

import sys
from getpass import getpass

from argon2 import PasswordHasher


def main() -> int:
    password = getpass("Owner password: ")
    if getpass("Repeat password: ") != password:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    if not password:
        print("Password must not be empty.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
