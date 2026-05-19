"""Bootstrap the first user (and reset passwords later).

    python -m app.cli.create_user --email you@example.com
    # prompts for password (typed twice, hidden)

If a user with the given email already exists, the CLI offers to reset the
password instead of erroring out. No web signup flow exists by design —
this is a single-user tool.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid

from sqlalchemy import select

from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.db.models import User
from app.db.session import SessionLocal


def _prompt_password() -> str:
    while True:
        pw = getpass.getpass("Password: ")
        if len(pw) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            continue
        pw2 = getpass.getpass("Password (again): ")
        if pw != pw2:
            print("Passwords didn't match — try again.", file=sys.stderr)
            continue
        return pw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or reset the local user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default=None)
    parser.add_argument(
        "--password",
        default=None,
        help="Skip interactive prompt — useful for automated provisioning. "
             "Min 8 characters.",
    )
    args = parser.parse_args(argv)
    configure_logging("INFO")
    log = get_logger("cli.create_user")

    email = args.email.lower().strip()
    pw = args.password or _prompt_password()
    if args.password and len(args.password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1

    with SessionLocal() as session:
        existing = session.execute(
            select(User).where(User.email == email)
        ).scalar_one_or_none()
        if existing is not None:
            confirm = input(
                f"User {email} already exists. Reset password? [y/N]: "
            ).strip().lower()
            if confirm not in ("y", "yes"):
                print("Aborted.", file=sys.stderr)
                return 1
            existing.password_hash = hash_password(pw)
            if args.display_name:
                existing.display_name = args.display_name
            session.commit()
            log.info("password_reset", email=email)
            print(f"Reset password for {email}.")
            return 0

        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name=args.display_name,
            password_hash=hash_password(pw),
        )
        session.add(user)
        session.commit()
        log.info("user_created", email=email, user_id=str(user.id))
        print(f"Created user {email} (id {user.id}).")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
