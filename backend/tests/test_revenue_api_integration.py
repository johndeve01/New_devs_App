"""
Live API regression tests (requires docker-compose stack).
Skipped automatically when the API is unreachable.
"""

import os

import httpx
import pytest

API_BASE = os.getenv("API_BASE", "http://localhost:8000")


def _api_available() -> bool:
    try:
        r = httpx.get(f"{API_BASE}/docs", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _api_available(), reason="API not running")


def _login(email: str, password: str) -> str:
    r = httpx.post(
        f"{API_BASE}/api/v1/auth/login",
        json={"email": email, "password": password},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _summary(token: str, property_id: str, month: int | None = 3, year: int | None = 2024):
    params = {"property_id": property_id}
    if month is not None:
        params["month"] = month
    if year is not None:
        params["year"] = year
    r = httpx.get(
        f"{API_BASE}/api/v1/dashboard/summary",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    r.raise_for_status()
    return r.json()


class TestCrossTenantIsolation:
    def test_shared_property_id_does_not_leak_across_tenants(self):
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        token_b = _login("ocean@propertyflow.com", "client_b_2024")

        a = _summary(token_a, "prop-001")
        b = _summary(token_b, "prop-001")

        assert a["total_revenue"] == "2250.000"
        assert a["reservations_count"] == 4
        # Tenant B owns prop-001 (Mountain Lodge) but has no March reservations.
        assert b["total_revenue"] in ("0.00", "0.000")
        assert b["reservations_count"] == 0
        assert a["total_revenue"] != b["total_revenue"]

    def test_client_switch_does_not_reuse_other_tenant_cache(self):
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        token_b = _login("ocean@propertyflow.com", "client_b_2024")

        # Prime cache as A, then B must still get isolated empty result.
        _summary(token_a, "prop-001")
        b = _summary(token_b, "prop-001")
        assert b["reservations_count"] == 0

        # And A still sees own data after B's request.
        a = _summary(token_a, "prop-001")
        assert a["reservations_count"] == 4


class TestMarchTimezoneTotals:
    def test_paris_march_includes_boundary_reservation(self):
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        a = _summary(token_a, "prop-001", month=3, year=2024)
        assert a["total_revenue"] == "2250.000"
        assert a["reservations_count"] == 4

    def test_february_utc_naive_window_would_differ(self):
        """February local Paris should include only bookings in Feb Paris time — none in seed."""
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        feb = _summary(token_a, "prop-001", month=2, year=2024)
        assert feb["reservations_count"] == 0


class TestDecimalPrecisionAPI:
    def test_total_revenue_is_decimal_string_not_float(self):
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        a = _summary(token_a, "prop-001")
        assert isinstance(a["total_revenue"], str)
        assert a["total_revenue"] == "2250.000"

    def test_milli_precision_sum_for_dec_reservations(self):
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        # prop-002 amounts are 2dp; still returned as quantized decimal string
        data = _summary(token_a, "prop-002")
        assert data["total_revenue"] == "4975.500"
        assert isinstance(data["total_revenue"], str)


class TestReportingPeriodDefaults:
    def test_omitting_month_year_returns_all_time_not_hardcoded_march(self):
        """Default contract: no period filter (all-time). Seed still totals 2250 for prop-001."""
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        data = _summary(token_a, "prop-001", month=None, year=None)
        assert data["total_revenue"] == "2250.000"
        assert data["reservations_count"] == 4
        assert "month" not in data
        assert "year" not in data

    def test_partial_period_rejected(self):
        token_a = _login("sunset@propertyflow.com", "client_a_2024")
        r = httpx.get(
            f"{API_BASE}/api/v1/dashboard/summary",
            params={"property_id": "prop-001", "month": 3},
            headers={"Authorization": f"Bearer {token_a}"},
            timeout=10.0,
        )
        assert r.status_code == 400
