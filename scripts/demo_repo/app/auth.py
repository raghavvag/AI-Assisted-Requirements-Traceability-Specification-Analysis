"""Authentication. Implements REQ-01 (User Authentication & Session Management)."""

from fastapi import APIRouter, HTTPException

from app import security
from app.models import LoginRequest, Session

router = APIRouter(prefix="/api/v1/auth")


@router.post("/login", response_model=Session)
def login(req: LoginRequest) -> Session:
    """Validate the credentials against the stored password hash and issue a signed JWT session."""
    user = security.verify_password(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token, session_id = security.issue_jwt(user.id)
    return Session(access_token=token, expires_in=security.TOKEN_TTL, session_id=session_id)


@router.post("/logout", status_code=204)
def logout(session_id: str) -> None:
    """Revoke the session so its JWT can no longer be used."""
    security.revoke_session(session_id)
