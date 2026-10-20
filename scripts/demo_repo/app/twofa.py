"""Two-factor step-up authentication. Implements REQ-09 (2FA enforcement for high-value operations)."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import otp_store

router = APIRouter(prefix="/api/v1/auth/2fa")

HIGH_VALUE_LIMIT = 500.0


class ChallengeRequest(BaseModel):
    operation: str = Field(..., description="High-value operation to authorise: place_order or add_payment_method")
    amount: float = Field(0.0, description="Order total; orders over $500 need two-factor verification")


class Challenge(BaseModel):
    challenge_token: str = Field(..., description="Token to submit with the one-time password (OTP)")
    expires_in: int = Field(..., description="Seconds until the OTP expires")


def requires_step_up(operation: str, amount: float) -> bool:
    """Two-factor authentication (2FA) is required for orders over $500 and for adding a new payment method."""
    return operation == "add_payment_method" or amount > HIGH_VALUE_LIMIT


@router.post("/challenge", response_model=Challenge)
def issue_challenge(req: ChallengeRequest, user_id: str) -> Challenge:
    """Issue a time-limited one-time password (OTP) challenge for a high-value operation that needs 2FA."""
    if not requires_step_up(req.operation, req.amount):
        raise HTTPException(status_code=400, detail="This operation does not need two-factor verification")
    token, ttl = otp_store.send_otp(user_id, req.operation)
    return Challenge(challenge_token=token, expires_in=ttl)


@router.post("/verify")
def verify_otp(challenge_token: str, otp: str) -> dict:
    """Verify the user's OTP and authorise the pending high-value operation."""
    if not otp_store.check(challenge_token, otp):
        raise HTTPException(status_code=401, detail="Invalid or expired one-time password")
    return {"verified": True, "challenge_token": challenge_token}


def is_verified(user_id: str, challenge_token: Optional[str]) -> bool:
    """True when the user completed the 2FA challenge identified by challenge_token."""
    return challenge_token is not None and otp_store.is_verified(user_id, challenge_token)
