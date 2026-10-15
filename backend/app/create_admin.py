"""
Create a clinic admin login, or reset an existing admin's password.

Usage: python -m app.create_admin --email staff@clinic.example --name "Dr. Lopez"
The password is read interactively (or from BAPC_NEW_ADMIN_PASSWORD for scripting).
"""

import argparse
import asyncio
import getpass
import os

from sqlalchemy import func, select

from app.database import async_session
from app.models import User, UserRole
from app.security import hash_password


async def create_admin(email: str, name: str, password: str) -> None:
    email = email.strip().lower()
    async with async_session() as db:
        user = (
            await db.execute(select(User).where(func.lower(User.email) == email))
        ).scalar_one_or_none()
        if user and user.role != UserRole.admin:
            raise SystemExit(f"{email} belongs to a patient account; use a different email.")
        if user:
            user.password_hash = hash_password(password)
            user.full_name = name or user.full_name
            action = "Updated"
        else:
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(password),
                    full_name=name or email,
                    role=UserRole.admin,
                )
            )
            action = "Created"
        await db.commit()
    print(f"{action} admin {email}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    args = parser.parse_args()
    password = os.environ.get("BAPC_NEW_ADMIN_PASSWORD") or getpass.getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("Password must be at least 8 characters.")
    asyncio.run(create_admin(args.email, args.name, password))


if __name__ == "__main__":
    main()
