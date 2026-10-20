"""
scripts/mint_report_token.py — Session tokens for UI automation
================================================================
The web app only signs in through Google OAuth, which a headless browser cannot
complete. This mints the same access/refresh JWT pair that POST /api/auth/google
returns, for a user already in the backend database, signed with SECRET_KEY from
backend/.env. Used by scripts/capture_report_screenshots.js.

It reads backend/.env and the backend database directly, so it only works for
someone who already has the server's secrets. It is a local dev tool.

Run:  python scripts/mint_report_token.py [--email you@example.com]
Prints one JSON object: {"access_token", "refresh_token", "user"}.
Without --email the first active user is used; if the database has no users, a
"Report Demo" user is created.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
# DATABASE_URL is sqlite:///./traceability.db, relative to where uvicorn runs.
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.core.security import create_access_token, create_refresh_token  # noqa: E402
from app.database import AsyncSessionLocal, User, engine, init_db  # noqa: E402
from app.schemas import UserOut  # noqa: E402


async def mint(email: str | None) -> dict:
    await init_db()
    async with AsyncSessionLocal() as db:
        if email:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None:
                raise SystemExit(f"No user with email {email!r}. Sign in once through the web app first.")
        else:
            user = (await db.execute(
                select(User).where(User.is_active.is_(True)).order_by(User.id).limit(1)
            )).scalar_one_or_none()
            if user is None:
                user = User(google_id="report-demo", email="report.demo@example.com", name="Report Demo")
                db.add(user)
                await db.commit()
                await db.refresh(user)
        claims = {"sub": str(user.id), "email": user.email}
        out = {
            "access_token": create_access_token(claims),
            "refresh_token": create_refresh_token(claims),
            "user": UserOut.model_validate(user).model_dump(mode="json"),
        }
    await engine.dispose()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--email", help="sign in as this existing user (default: first active user)")
    print(json.dumps(asyncio.run(mint(ap.parse_args().email))))
