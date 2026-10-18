import os
import pytest
import pytest_asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, update

# Configure test environment
os.environ["TESTING"] = "true"
os.environ["BREVO_API_KEY"] = "mock_key"
os.environ["COOKIE_SECURE"] = "false"

from app.core.config import settings
settings.TESTING = True
settings.BREVO_API_KEY = "mock_key"
settings.COOKIE_SECURE = False

from app.main import app
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.otp import OtpCode, OtpPurpose
from app.models.token import RefreshToken
from app.services.otp import hash_otp_code


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_signup_creates_unverified_user(async_client: AsyncClient):
    """Test signup creates an unverified user and returns 201 without tokens."""
    email = "test_signup_user@example.com"
    password = "SecurePassword123!"

    # Clean up any leftover test data
    async with AsyncSessionLocal() as db:
        await db.execute(select(User).where(User.email == email))
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    resp = await async_client.post("/auth/signup", json={"email": email, "password": password})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "success"
    assert data["email"] == email
    assert "access_token" not in resp.cookies
    assert "refresh_token" not in resp.cookies

    # Verify user state in DB
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one_or_none()
        assert user is not None
        assert user.is_verified is False
        assert user.is_active is True

    # Duplicate signup should return 409
    resp_dup = await async_client.post("/auth/signup", json={"email": email, "password": password})
    assert resp_dup.status_code == 409
    dup_data = resp_dup.json()
    assert dup_data["status"] == "error"
    assert "already registered" in dup_data["error"]


@pytest.mark.asyncio
async def test_login_blocked_when_unverified(async_client: AsyncClient):
    """Test login is blocked with 403 unverified when email has not been verified."""
    email = "unverified_login_user@example.com"
    password = "SecurePassword123!"

    # Clean up and create unverified user
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    signup_res = await async_client.post("/auth/signup", json={"email": email, "password": password})
    assert signup_res.status_code == 201

    # Attempt login
    login_res = await async_client.post("/auth/login", json={"email": email, "password": password})
    assert login_res.status_code == 403
    data = login_res.json()
    assert data["status"] == "error"
    assert data["error"] == "unverified"
    assert "access_token" not in login_res.cookies


@pytest.mark.asyncio
async def test_verify_otp_with_correct_code_verifies_and_logs_in(async_client: AsyncClient):
    """Test verify-otp with correct code marks user verified and sets auth cookies."""
    email = "otp_verify_user@example.com"
    password = "SecurePassword123!"

    # Clean up
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    await async_client.post("/auth/signup", json={"email": email, "password": password})

    # Retrieve user and set a known OTP code in DB
    test_code = "789123"
    code_hash = hash_otp_code(test_code)
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one()
        otp = OtpCode(
            user_id=user.id,
            purpose=OtpPurpose.EMAIL_VERIFICATION,
            code_hash=code_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            attempts=0,
            consumed=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(otp)
        await db.commit()

    # Verify with correct code
    resp = await async_client.post("/auth/verify-otp", json={
        "email": email,
        "code": test_code,
        "purpose": "email_verification",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert "access_token" in resp.cookies
    assert "refresh_token" in resp.cookies

    # Verify user is now verified in DB
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one()
        assert user.is_verified is True


@pytest.mark.asyncio
async def test_wrong_code_5_times_kills_the_code(async_client: AsyncClient):
    """Test 5 failed attempts locks and invalidates the OTP code."""
    email = "five_attempts_user@example.com"
    password = "SecurePassword123!"

    # Clean up
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    await async_client.post("/auth/signup", json={"email": email, "password": password})

    correct_code = "654321"
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one()
        otp = OtpCode(
            user_id=user.id,
            purpose=OtpPurpose.EMAIL_VERIFICATION,
            code_hash=hash_otp_code(correct_code),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            attempts=0,
            consumed=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(otp)
        await db.commit()

    # 4 failed attempts
    for _ in range(4):
        res = await async_client.post("/auth/verify-otp", json={
            "email": email,
            "code": "000000",
            "purpose": "email_verification",
        })
        assert res.status_code == 400

    # 5th failed attempt kills the code
    res5 = await async_client.post("/auth/verify-otp", json={
        "email": email,
        "code": "000000",
        "purpose": "email_verification",
    })
    assert res5.status_code == 400

    # Submitting the correct code now must still fail because the code is exhausted
    res_correct = await async_client.post("/auth/verify-otp", json={
        "email": email,
        "code": correct_code,
        "purpose": "email_verification",
    })
    assert res_correct.status_code == 400


@pytest.mark.asyncio
async def test_resend_respects_cooldown(async_client: AsyncClient):
    """Test resending OTP within 60s cooldown returns 429."""
    email = "cooldown_test_user@example.com"
    password = "SecurePassword123!"

    # Clean up
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    await async_client.post("/auth/signup", json={"email": email, "password": password})

    # Immediate resend should hit 60s cooldown
    resend_res = await async_client.post("/auth/resend-otp", json={
        "email": email,
        "purpose": "email_verification",
    })
    assert resend_res.status_code == 429
    data = resend_res.json()
    assert data["status"] == "error"
    assert "retry_after" in data
    assert data["retry_after"] > 0


@pytest.mark.asyncio
async def test_refresh_rotation_invalidates_old_token(async_client: AsyncClient):
    """Test refresh token rotation issues a new pair and revokes the old refresh token."""
    email = "refresh_rotation_user@example.com"
    password = "SecurePassword123!"

    # Clean up
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    await async_client.post("/auth/signup", json={"email": email, "password": password})

    # Set user verified
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one()
        user.is_verified = True
        await db.commit()

    # Login to get first token pair
    login_res = await async_client.post("/auth/login", json={"email": email, "password": password})
    assert login_res.status_code == 200
    first_refresh = login_res.cookies.get("refresh_token")
    assert first_refresh is not None

    # Refresh tokens using the first refresh token
    async_client.cookies.set("refresh_token", first_refresh)
    refresh_res = await async_client.post("/auth/refresh")
    assert refresh_res.status_code == 200
    second_refresh = refresh_res.cookies.get("refresh_token")
    assert second_refresh is not None
    assert second_refresh != first_refresh

    # Attempt to reuse the first refresh token (should fail with 401 because it was revoked)
    async_client.cookies.set("refresh_token", first_refresh)
    reuse_res = await async_client.post("/auth/refresh")
    assert reuse_res.status_code == 401


@pytest.mark.asyncio
async def test_get_me_and_logout(async_client: AsyncClient):
    """Test GET /auth/me returns current user and POST /auth/logout clears cookies."""
    email = "me_logout_user@example.com"
    password = "SecurePassword123!"

    # Clean up
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        u = user_res.scalar_one_or_none()
        if u:
            await db.delete(u)
            await db.commit()

    await async_client.post("/auth/signup", json={"email": email, "password": password})
    async with AsyncSessionLocal() as db:
        user_res = await db.execute(select(User).where(User.email == email))
        user = user_res.scalar_one()
        user.is_verified = True
        await db.commit()

    login_res = await async_client.post("/auth/login", json={"email": email, "password": password})
    assert login_res.status_code == 200

    # Call /auth/me
    me_res = await async_client.get("/auth/me")
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["status"] == "success"
    assert me_data["user"]["email"] == email

    # Logout
    logout_res = await async_client.post("/auth/logout")
    assert logout_res.status_code == 200

    # Now /auth/me should fail with 401
    me_after = await async_client.get("/auth/me")
    assert me_after.status_code == 401
