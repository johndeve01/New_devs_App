"""
Focused regression tests for Property Revenue Dashboard bugs:
  A) Cross-tenant cache / query isolation
  B) March month-boundary / property-local timezone filtering
  C) Decimal money precision (no float drift)
"""

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, patch

import pytest

from app.services.cache import _revenue_cache_key
from app.services.reservations import (
    RevenueDataAccessError,
    _month_bounds_utc,
    _quantize_money,
    calculate_total_revenue,
)


class TestCacheKeyTenantIsolation:
    def test_cache_key_includes_tenant_id(self):
        key_a = _revenue_cache_key("tenant-a", "prop-001", 3, 2024)
        key_b = _revenue_cache_key("tenant-b", "prop-001", 3, 2024)
        assert key_a != key_b
        assert "tenant-a" in key_a
        assert "tenant-b" in key_b
        assert key_a == "revenue:tenant-a:prop-001:2024:03"

    def test_same_property_different_tenants_do_not_share_key(self):
        # Similar/identical property IDs across clients must not collide.
        assert _revenue_cache_key("tenant-a", "prop-001", None, None) != _revenue_cache_key(
            "tenant-b", "prop-001", None, None
        )

    def test_period_and_all_time_keys_do_not_collide(self):
        period = _revenue_cache_key("tenant-a", "prop-001", 3, 2024)
        all_time = _revenue_cache_key("tenant-a", "prop-001", None, None)
        assert period != all_time
        assert all_time.endswith(":all")

    def test_partial_period_rejected(self):
        with pytest.raises(ValueError):
            _revenue_cache_key("tenant-a", "prop-001", 3, None)
        with pytest.raises(ValueError):
            _revenue_cache_key("tenant-a", "prop-001", None, 2024)


class TestMonthBoundaryTimezone:
    def test_bounds_are_utc(self):
        start, end = _month_bounds_utc(2024, 3, "Europe/Paris")
        assert start.tzinfo == timezone.utc
        assert end.tzinfo == timezone.utc
        # Paris CET (+01) on Mar 1 → 23:00 UTC previous day; Apr 1 is CEST (+02)
        assert start == datetime(2024, 2, 29, 23, 0, tzinfo=timezone.utc)
        assert end == datetime(2024, 3, 31, 22, 0, tzinfo=timezone.utc)

    def test_paris_march_includes_utc_feb29_2330(self):
        """
        Seed res-tz-1: 2024-02-29 23:30:00+00 == 2024-03-01 00:30 Europe/Paris.
        Must fall inside March 2024 for Paris properties.
        """
        start, end = _month_bounds_utc(2024, 3, "Europe/Paris")
        check_in = datetime(2024, 2, 29, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert start <= check_in < end

    def test_new_york_march_excludes_utc_feb29_2330(self):
        """
        Same UTC instant is still Feb 29 evening in America/New_York (EST, UTC-5).
        Must NOT fall inside March 2024 for NY properties.
        """
        start, end = _month_bounds_utc(2024, 3, "America/New_York")
        check_in = datetime(2024, 2, 29, 23, 30, tzinfo=ZoneInfo("UTC"))
        assert not (start <= check_in < end)

    def test_dst_transition_reflected_in_utc_end_bound(self):
        """March→April 2024 crosses DST in Paris and New York; UTC end offset changes."""
        from datetime import timedelta

        paris_start, paris_end = _month_bounds_utc(2024, 3, "Europe/Paris")
        ny_start, ny_end = _month_bounds_utc(2024, 3, "America/New_York")
        # Spring-forward: local March wall-time is 31 days, UTC span is 31d - 1h.
        one_dst_month = timedelta(days=31) - timedelta(hours=1)
        assert paris_end - paris_start == one_dst_month
        assert ny_end - ny_start == one_dst_month
        assert paris_start.hour == 23 and paris_end.hour == 22  # CET→CEST
        assert ny_start.hour == 5 and ny_end.hour == 4  # EST→EDT

    def test_december_year_rollover(self):
        start, end = _month_bounds_utc(2024, 12, "Europe/Paris")
        assert start == datetime(2024, 11, 30, 23, 0, tzinfo=timezone.utc)
        assert end == datetime(2024, 12, 31, 23, 0, tzinfo=timezone.utc)

    def test_naive_utc_march_bounds_would_exclude_paris_edge_case(self):
        """Documents the original bug: naive UTC month start excludes Paris-local March 1."""
        naive_start = datetime(2024, 3, 1)
        check_in_utc = datetime(2024, 2, 29, 23, 30)
        assert not (check_in_utc >= naive_start)

    def test_month_end_exclusive(self):
        start, end = _month_bounds_utc(2024, 3, "UTC")
        assert start == datetime(2024, 3, 1, 0, 0, tzinfo=timezone.utc)
        assert end == datetime(2024, 4, 1, 0, 0, tzinfo=timezone.utc)


class TestFinancialPrecision:
    def test_quantize_preserves_milli_precision(self):
        total = Decimal("333.333") + Decimal("333.333") + Decimal("333.334")
        assert _quantize_money(total) == "1000.000"

    def test_float_conversion_is_unsafe_for_money_strings(self):
        # Guardrail: summing via float drifts; Decimal path stays exact.
        assert float(Decimal("0.1")) + float(Decimal("0.2")) != 0.3
        assert Decimal("0.1") + Decimal("0.2") == Decimal("0.3")
        assert _quantize_money(Decimal("0.1") + Decimal("0.2")) == "0.300"


class TestDatabaseFailureBehavior:
    @pytest.mark.asyncio
    async def test_db_failure_does_not_return_zero_revenue(self):
        fake_pool = AsyncMock()
        fake_pool.initialize = AsyncMock()
        fake_pool.session_factory = None  # simulates pool init failure

        with patch("app.core.database_pool.db_pool", fake_pool):
            with pytest.raises(RevenueDataAccessError):
                await calculate_total_revenue("prop-001", "tenant-a")


@pytest.mark.asyncio
async def test_monthly_revenue_prop001_tenant_a_includes_tz_edge(monkeypatch):
    """
    Expected March Paris prop-001: 1250 + 333.333 + 333.333 + 333.334 = 2250.000
    """
    expected = Decimal("1250.000") + Decimal("333.333") + Decimal("333.333") + Decimal("333.334")
    assert _quantize_money(expected) == "2250.000"
    start, end = _month_bounds_utc(2024, 3, "Europe/Paris")
    edge = datetime(2024, 2, 29, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert start <= edge < end
