"""
backend/app/routers/auth.py
============================
Google OAuth 2.0 endpoint:
  POST /api/auth/google  — verify ID token → get-or-create User → issue JWT
  POST /api/auth/refresh — rotate access token using refresh token
  GET  /api/auth/me      — return current user profile
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import create_access_token, create_refresh_token, decode_token, get_current_user
from app.database import User, get_db
from app.schemas import GoogleAuthRequest, TokenResponse, UserOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helper: verify Google ID token
# ---------------------------------------------------------------------------

def _verify_google_token(id_token_str: str) -> dict:
    """
    Verify the Google ID token and return its payload.
    Raises HTTPException on failure with an actionable message.
    """
    if not settings.GOOGLE_CLIENT_ID or "your-project" in settings.GOOGLE_CLIENT_ID:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Server misconfigured: GOOGLE_CLIENT_ID is not set in backend/.env. "
                "Copy backend/.env.example to backend/.env and set the same value as "
                "the frontend's VITE_GOOGLE_CLIENT_ID, then restart the backend."
            ),
        )
    try:
        info = google_id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            audience=settings.GOOGLE_CLIENT_ID,
        )
        return info
    except ValueError as e:
        logger.warning("Invalid Google ID token: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Google rejected the ID token ({e}). Common causes: the token's "
                "audience does not match GOOGLE_CLIENT_ID (frontend and backend "
                "client IDs differ), or your account is not an OAuth 'test user'."
            ),
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/google", response_model=TokenResponse, summary="Exchange Google ID token for JWT")
async def google_auth(payload: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    """
    1. Verify the Google-issued ID token with Google's public keys.
    2. Extract email, name, google_id, avatar_url from token claims.
    3. Upsert the User record (create if first login, update avatar if changed).
    4. Issue a custom access + refresh JWT pair.
    """
    google_payload = _verify_google_token(payload.id_token)

    google_id  = google_payload["sub"]
    email      = google_payload.get("email", "")
    name       = google_payload.get("name", email.split("@")[0])
    avatar_url = google_payload.get("picture")

    # Get or create user
    result = await db.execute(select(User).where(User.google_id == google_id))
    user   = result.scalar_one_or_none()

    if user is None:
        user = User(google_id=google_id, email=email, name=name, avatar_url=avatar_url)
        db.add(user)
        await db.flush()
        logger.info("New user registered: %s (%s)", name, email)
    else:
        user.avatar_url = avatar_url  # refresh in case Google updated it
        logger.info("Existing user logged in: %s", email)

    await db.commit()
    await db.refresh(user)

    token_data     = {"sub": str(user.id), "email": user.email}
    access_token   = create_access_token(token_data)
    refresh_token  = create_refresh_token(token_data)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", summary="Rotate access token using refresh token")
async def refresh_access_token(refresh_token: str, db: AsyncSession = Depends(get_db)):
    """Issue a fresh access token if the refresh token is valid."""
    payload = decode_token(refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Not a refresh token.")

    user_id = payload.get("sub")
    result  = await db.execute(select(User).where(User.id == int(user_id)))
    user    = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive or deleted.")

    new_access = create_access_token({"sub": str(user.id), "email": user.email})
    return {"access_token": new_access, "token_type": "bearer"}


@router.get("/me", response_model=UserOut, summary="Get current user profile")
async def get_me(current_user: User = Depends(get_current_user)):
    return UserOut.model_validate(current_user)
