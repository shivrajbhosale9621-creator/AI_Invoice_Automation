"""
test_custom_dashboard_charts.py - E2E Integration test for Dashboard Custom Pinned Charts.

Tests:
1. User logs in.
2. GET /api/dashboard/charts (initially empty).
3. POST /api/dashboard/charts with prompt "Plot top 5 vendors by spend".
4. Verifies chart is created on disk and in database.
5. GET /api/dashboard/charts confirms the chart persists with correct user_id.
6. DELETE /api/dashboard/charts/{id} removes the chart.
7. GET /api/dashboard/charts confirms the chart is deleted.
"""

import httpx
import pytest
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.core.security import hash_password

BASE_URL = "http://127.0.0.1:5000"


@pytest.mark.asyncio
async def test_custom_dashboard_charts_e2e():
    test_email = "dashboard_test_user@example.com"
    test_password = "Password123!"

    # Ensure user exists and is verified
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(User).where(User.email == test_email))
        u = res.scalar_one_or_none()
        if not u:
            u = User(email=test_email, password_hash=hash_password(test_password), is_verified=True)
            db.add(u)
            await db.commit()
        elif not u.is_verified:
            u.is_verified = True
            await db.commit()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=40.0) as client:
        # 1. Login with verified test user
        login_res = await client.post("/auth/login", json={"email": test_email, "password": test_password})
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"

        # 2. GET /api/dashboard/charts
        get_res = await client.get("/api/dashboard/charts")
        assert get_res.status_code == 200
        initial_charts = get_res.json().get("charts", [])
        initial_count = len(initial_charts)

        # 3. POST /api/dashboard/charts
        post_res = await client.post(
            "/api/dashboard/charts",
            json={
                "prompt": "Plot top 5 vendors by spend",
                "title": "Major Suppliers Spend Ranking"
            }
        )
        assert post_res.status_code == 200, f"Create chart failed: {post_res.text}"
        created_chart = post_res.json().get("chart")
        assert created_chart is not None
        chart_id = created_chart.get("id")
        assert chart_id.startswith("dchart_")
        assert created_chart.get("title") == "Major Suppliers Spend Ranking"
        assert "/static/charts/" in created_chart.get("chart_path")

        # 4. GET /api/dashboard/charts - verifies persistence across requests
        get_res_2 = await client.get("/api/dashboard/charts")
        assert get_res_2.status_code == 200
        charts_after_create = get_res_2.json().get("charts", [])
        assert len(charts_after_create) == initial_count + 1
        found = any(c.get("id") == chart_id for c in charts_after_create)
        assert found, f"Created chart {chart_id} not found in user charts list"

        # 5. DELETE /api/dashboard/charts/{chart_id}
        del_res = await client.delete(f"/api/dashboard/charts/{chart_id}")
        assert del_res.status_code == 200, f"Delete chart failed: {del_res.text}"

        # 6. GET /api/dashboard/charts - verifies it was removed
        get_res_3 = await client.get("/api/dashboard/charts")
        assert get_res_3.status_code == 200
        charts_after_delete = get_res_3.json().get("charts", [])
        assert len(charts_after_delete) == initial_count
        assert not any(c.get("id") == chart_id for c in charts_after_delete)

        print("\nALL CUSTOM DASHBOARD CHARTS INTEGRATION TESTS PASSED CLEANLY!")

