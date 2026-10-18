import uuid
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Request, Response, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limiter import limiter
from app.core.security import (
    hash_password,
    verify_password,
    hash_token,
    generate_refresh_token,
    create_access_token,
    set_auth_cookies,
    clear_auth_cookies,
    get_current_user,
)
from app.models.user import User
from app.models.otp import OtpCode, OtpPurpose
from app.models.token import RefreshToken
from app.services.mailer import send_otp_email, BrevoEmailError
from app.services.otp import (
    create_and_store_otp,
    verify_and_consume_otp,
    OtpCooldownError,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────────────────────────────────────────

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, description="6-digit verification code")
    purpose: OtpPurpose = OtpPurpose.EMAIL_VERIFICATION


class ResendOtpRequest(BaseModel):
    email: EmailStr
    purpose: OtpPurpose = OtpPurpose.EMAIL_VERIFICATION


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Issue and Record Tokens
# ─────────────────────────────────────────────────────────────────────────────

async def issue_tokens_for_user(
    db: AsyncSession,
    user: User,
    response: Response,
) -> tuple[str, str]:
    """Issue access token + refresh token, persist refresh hash, and set cookies."""
    now = datetime.now(timezone.utc)
    access_token = create_access_token(user_id=user.id, email=user.email)
    raw_refresh_token = generate_refresh_token()
    token_digest = hash_token(raw_refresh_token)
    expires_at = now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)

    db_token = RefreshToken(
        user_id=user.id,
        token_hash=token_digest,
        expires_at=expires_at,
        revoked=False,
        created_at=now,
    )
    db.add(db_token)
    await db.commit()

    set_auth_cookies(response, access_token=access_token, refresh_token=raw_refresh_token)
    return access_token, raw_refresh_token


# ─────────────────────────────────────────────────────────────────────────────
# 1. POST /auth/signup
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)):
    """
    Register a new user:
    - Validate email and password (min 8 chars)
    - If email exists -> 409 error
    - Hash password with Argon2id
    - Create user with is_verified=False
    - Generate 6-digit OTP and send via Brevo
    """
    clean_email = payload.email.lower().strip()

    # Check if user already exists
    existing = await db.execute(select(User).where(User.email == clean_email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"status": "error", "error": "Email already registered"},
        )

    # Hash password with Argon2id
    pwd_hash = hash_password(payload.password)
    user = User(
        email=clean_email,
        password_hash=pwd_hash,
        is_verified=False,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Generate OTP code & dispatch email
    try:
        otp_code = await create_and_store_otp(
            db=db,
            user_id=user.id,
            purpose=OtpPurpose.EMAIL_VERIFICATION,
            enforce_cooldown=False,
        )
        await send_otp_email(clean_email, otp_code, purpose="email_verification")
    except BrevoEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"status": "error", "error": "could not send email, use resend"},
        ) from exc

    return {
        "status": "success",
        "message": "verification code sent",
        "email": clean_email,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. POST /auth/verify-otp
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/verify-otp")
@limiter.limit("10/minute")
async def verify_otp(
    request: Request,
    response: Response,
    payload: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Verify 6-digit OTP code:
    - On success: mark consumed, set is_verified=true, issue auth tokens & cookies.
    - On failure: increment attempts, return 400 "invalid or expired code".
    """
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_error = {"status": "error", "error": "invalid or expired code"}

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    verified = await verify_and_consume_otp(
        db=db,
        user_id=user.id,
        code=payload.code,
        purpose=payload.purpose,
    )

    if not verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    # Success: mark user verified if it was email verification
    if payload.purpose == OtpPurpose.EMAIL_VERIFICATION:
        user.is_verified = True
        await db.commit()
        await db.refresh(user)

    # Issue access + refresh tokens
    await issue_tokens_for_user(db, user, response)

    return {"status": "success", "message": "verified"}


# ─────────────────────────────────────────────────────────────────────────────
# 3. POST /auth/resend-otp
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/resend-otp")
@limiter.limit("3/minute")
async def resend_otp(
    request: Request,
    payload: ResendOtpRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Resend OTP:
    - Enforces 60-second cooldown (returns 429 with retry_after).
    - If user exists: invalidate older codes, generate new code, send email.
    - Always returns 200 generic message even if user does not exist (no enumeration).
    """
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_response = {
        "status": "success",
        "message": "If the account exists, a code was sent",
    }

    if not user:
        return generic_response

    try:
        otp_code = await create_and_store_otp(
            db=db,
            user_id=user.id,
            purpose=payload.purpose,
            enforce_cooldown=True,
        )
        await send_otp_email(clean_email, otp_code, purpose=payload.purpose.value)
    except OtpCooldownError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "status": "error",
                "error": "Please wait before requesting another code",
                "retry_after": exc.retry_after,
            },
        )
    except BrevoEmailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"status": "error", "error": "could not send email, use resend"},
        ) from exc

    return generic_response


# ─────────────────────────────────────────────────────────────────────────────
# 4. POST /auth/login
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate user:
    - Verify password with constant-time delay on failure.
    - If user unverified: generate verification OTP and return 403 "unverified".
    - On success: issue auth cookies.
    """
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(payload.password, user.password_hash):
        await asyncio.sleep(0.1)  # small constant-time delay on failure
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "Invalid email or password"},
        )

    if not user.is_verified:
        # User not verified: dispatch fresh OTP and return 403
        try:
            otp_code = await create_and_store_otp(
                db=db,
                user_id=user.id,
                purpose=OtpPurpose.EMAIL_VERIFICATION,
                enforce_cooldown=False,
            )
            await send_otp_email(clean_email, otp_code, purpose="email_verification")
        except Exception:
            pass  # best effort on email dispatch

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "status": "error",
                "error": "unverified",
                "message": "verify your email first",
                "email": clean_email,
            },
        )

    # Success: issue tokens and set cookies
    await issue_tokens_for_user(db, user, response)

    return {
        "status": "success",
        "message": "Logged in successfully",
        "user": {
            "id": str(user.id),
            "email": user.email,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. POST /auth/forgot-password
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Initiate password reset:
    - Always returns generic 200 message (no enumeration).
    - If user exists and is verified: sends password_reset OTP.
    """
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_response = {
        "status": "success",
        "message": "if the account exists, a code was sent",
    }

    if user and user.is_verified:
        try:
            otp_code = await create_and_store_otp(
                db=db,
                user_id=user.id,
                purpose=OtpPurpose.PASSWORD_RESET,
                enforce_cooldown=False,
            )
            await send_otp_email(clean_email, otp_code, purpose="password_reset")
        except BrevoEmailError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"status": "error", "error": "could not send email, use resend"},
            ) from exc

    return generic_response


# ─────────────────────────────────────────────────────────────────────────────
# 6. POST /auth/reset-password
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Reset password with OTP:
    - Verifies OTP for purpose=password_reset
    - Sets new Argon2id password hash
    - Revokes all existing refresh tokens
    - Consumes code
    """
    clean_email = payload.email.lower().strip()
    result = await db.execute(select(User).where(User.email == clean_email))
    user = result.scalar_one_or_none()

    generic_error = {"status": "error", "error": "invalid or expired code"}

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    verified = await verify_and_consume_otp(
        db=db,
        user_id=user.id,
        code=payload.code,
        purpose=OtpPurpose.PASSWORD_RESET,
    )

    if not verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=generic_error)

    # Set new password hash
    user.password_hash = hash_password(payload.new_password)

    # Revoke all existing refresh tokens for this user
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .values(revoked=True)
    )
    await db.commit()

    return {
        "status": "success",
        "message": "Password reset successfully. Please log in with your new password.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# 7. POST /auth/refresh
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/refresh")
async def refresh_tokens(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Token refresh & rotation:
    - Reads refresh token from httpOnly cookie
    - Hashes with sha256 and checks DB (must not be revoked or expired)
    - ROTATION: revokes the old refresh token
    - Issues brand new access + refresh token pair
    """
    raw_token = request.cookies.get("refresh_token")
    if not raw_token:
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "Refresh token missing"},
        )

    now = datetime.now(timezone.utc)
    token_digest = hash_token(raw_token)

    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == token_digest,
        RefreshToken.revoked == False,
        RefreshToken.expires_at > now,
    )
    result = await db.execute(stmt)
    db_token = result.scalar_one_or_none()

    if not db_token:
        clear_auth_cookies(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "Invalid or expired refresh token"},
        )

    # Rotate: revoke old token
    db_token.revoked = True

    # Fetch user
    user_result = await db.execute(select(User).where(User.id == db_token.user_id))
    user = user_result.scalar_one_or_none()

    if not user or not user.is_active:
        clear_auth_cookies(response)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"status": "error", "error": "User account is unavailable"},
        )

    # Issue brand new pair
    await issue_tokens_for_user(db, user, response)

    return {"status": "success", "message": "Tokens refreshed"}


# ─────────────────────────────────────────────────────────────────────────────
# 8. GET /auth/me
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/me")
async def get_me(user: User = Depends(get_current_user)):
    """Return authenticated user profile from access token cookie."""
    return {
        "status": "success",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "is_verified": user.is_verified,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. POST /auth/logout
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """
    Log out user:
    - Revoke refresh token in database if found
    - Clear both httpOnly cookies
    """
    raw_token = request.cookies.get("refresh_token")
    if raw_token:
        token_digest = hash_token(raw_token)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_digest)
            .values(revoked=True)
        )
        await db.commit()

    clear_auth_cookies(response)
    return {"status": "success", "message": "Logged out successfully"}
