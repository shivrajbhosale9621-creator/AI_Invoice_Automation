import uuid
import secrets
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.otp import OtpCode, OtpPurpose

OTP_EXPIRY_MINUTES = 10
MAX_ATTEMPTS = 5
RESEND_COOLDOWN_SECONDS = 60


class OtpCooldownError(Exception):
    """Raised when OTP resend is attempted before the cooldown elapsed."""
    def __init__(self, retry_after: int):
        super().__init__(f"Please wait {retry_after} seconds before requesting a new code.")
        self.retry_after = retry_after


class OtpInvalidError(Exception):
    """Raised when OTP is invalid, expired, or max attempts exceeded."""
    pass


def generate_otp() -> str:
    """Generate a 6-digit cryptographic OTP string from secrets module (never pseudo-random)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(code: str) -> str:
    """Compute sha256 hex digest of the OTP code."""
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def verify_code_hash(stored_hash: str, code: str) -> bool:
    """Compare candidate OTP code digest with stored digest in constant time."""
    candidate_hash = hash_otp_code(code)
    return hmac.compare_digest(stored_hash, candidate_hash)


async def check_cooldown(db: AsyncSession, user_id: uuid.UUID, purpose: OtpPurpose) -> None:
    """
    Check if the user is in cooldown period for this purpose.
    Raises OtpCooldownError with retry_after seconds if within 60s of last creation.
    """
    stmt = (
        select(OtpCode)
        .where(OtpCode.user_id == user_id, OtpCode.purpose == purpose)
        .order_by(desc(OtpCode.created_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    latest_otp = result.scalar_one_or_none()

    if latest_otp:
        now = datetime.now(timezone.utc)
        created_at = latest_otp.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        elapsed = (now - created_at).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            retry_after = max(1, int(RESEND_COOLDOWN_SECONDS - elapsed))
            raise OtpCooldownError(retry_after)


async def create_and_store_otp(
    db: AsyncSession,
    user_id: uuid.UUID,
    purpose: OtpPurpose,
    enforce_cooldown: bool = False,
) -> str:
    """
    Generate, hash, and store a new 6-digit OTP code for a user and purpose.
    - Optionally enforces 60-second cooldown (for resend requests).
    - Invalidates all existing unconsumed codes for this user and purpose.
    - Sets 10-minute expiry and attempts=0.
    - Returns the plaintext code to be delivered via email (never logged or stored).
    """
    now = datetime.now(timezone.utc)

    if enforce_cooldown:
        await check_cooldown(db, user_id, purpose)

    # Invalidate older unconsumed codes for this user and purpose
    invalidate_stmt = (
        update(OtpCode)
        .where(
            OtpCode.user_id == user_id,
            OtpCode.purpose == purpose,
            OtpCode.consumed == False,
        )
        .values(consumed=True)
    )
    await db.execute(invalidate_stmt)

    # Generate 6-digit code
    raw_code = generate_otp()
    code_digest = hash_otp_code(raw_code)
    expires_at = now + timedelta(minutes=OTP_EXPIRY_MINUTES)

    new_otp = OtpCode(
        user_id=user_id,
        purpose=purpose,
        code_hash=code_digest,
        expires_at=expires_at,
        attempts=0,
        consumed=False,
        created_at=now,
    )
    db.add(new_otp)
    await db.commit()

    return raw_code


async def verify_and_consume_otp(
    db: AsyncSession,
    user_id: uuid.UUID,
    code: str,
    purpose: OtpPurpose,
) -> bool:
    """
    Verify candidate code against active unconsumed OTP for user and purpose.
    - Increments attempts counter on failure.
    - If attempts >= 5, marks consumed/dead.
    - On success, marks consumed=True and returns True.
    - Returns False on any verification failure.
    """
    now = datetime.now(timezone.utc)

    stmt = (
        select(OtpCode)
        .where(
            OtpCode.user_id == user_id,
            OtpCode.purpose == purpose,
            OtpCode.consumed == False,
            OtpCode.expires_at > now,
        )
        .order_by(desc(OtpCode.created_at))
        .limit(1)
    )
    result = await db.execute(stmt)
    otp = result.scalar_one_or_none()

    if not otp:
        return False

    if otp.attempts >= MAX_ATTEMPTS:
        otp.consumed = True
        await db.commit()
        return False

    is_valid = verify_code_hash(otp.code_hash, code)

    if is_valid:
        otp.consumed = True
        await db.commit()
        return True
    else:
        otp.attempts += 1
        if otp.attempts >= MAX_ATTEMPTS:
            otp.consumed = True
        await db.commit()
        return False
