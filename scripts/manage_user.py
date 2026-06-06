"""Create or reset an application user from the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal
from app.models.user import User
from app.services.auth_service import create_user, get_password_hash, get_user_by_email


def list_users(db) -> None:
    users = db.query(User).order_by(User.id).all()
    if not users:
        print("No users found.")
        return

    for user in users:
        name = user.full_name or "-"
        print(f"{user.id}\t{user.email}\t{name}")


def create_or_reset_user(
    db,
    email: str,
    password: str,
    full_name: str | None = None,
    reset: bool = False,
) -> User:
    existing = get_user_by_email(db, email)
    if existing:
        if not reset:
            raise SystemExit(f"User already exists: {email}. Pass --reset to update password.")
        existing.password_hash = get_password_hash(password)
        if full_name is not None:
            existing.full_name = full_name
        db.commit()
        db.refresh(existing)
        return existing

    return create_user(db, email=email, password=password, full_name=full_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage SEO app users")
    parser.add_argument("--list", action="store_true", help="List users")
    parser.add_argument("--email", help="User email")
    parser.add_argument("--password", help="User password")
    parser.add_argument("--name", dest="full_name", default=None, help="Full name")
    parser.add_argument("--reset", action="store_true", help="Reset password for existing user")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.list:
            list_users(db)
            return

        if not args.email or not args.password:
            parser.error("--email and --password are required unless using --list")

        user = create_or_reset_user(
            db,
            email=args.email,
            password=args.password,
            full_name=args.full_name,
            reset=args.reset,
        )
        action = "Reset" if args.reset else "Created"
        print(f"{action} user:")
        print(f"  Email: {user.email}")
        print(f"  Name:  {user.full_name or '-'}")
        print(f"  Password: {args.password}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
