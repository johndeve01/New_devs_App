from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import text


def _month_bounds_utc(year: int, month: int, timezone_name: str) -> tuple[datetime, datetime]:
    """
    Build [start, end) month bounds in the property's local timezone, returned as
    timezone-aware datetimes (safe to compare with timestamptz check_in_date).
    """
    tz = ZoneInfo(timezone_name)
    start_local = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    if month < 12:
        end_local = datetime(year, month + 1, 1, 0, 0, 0, tzinfo=tz)
    else:
        end_local = datetime(year + 1, 1, 1, 0, 0, 0, tzinfo=tz)
    return start_local, end_local


def _empty_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": "0.00",
        "currency": "USD",
        "count": 0,
    }


def _quantize_money(amount: Decimal) -> str:
    """Serialize money with stable decimal places (match NUMERIC scale in DB)."""
    return str(amount.quantize(Decimal("0.001")))


async def _get_property_timezone(session, property_id: str, tenant_id: str) -> Optional[str]:
    result = await session.execute(
        text(
            """
            SELECT timezone
            FROM properties
            WHERE id = :property_id AND tenant_id = :tenant_id
            """
        ),
        {"property_id": property_id, "tenant_id": tenant_id},
    )
    row = result.fetchone()
    return row.timezone if row else None


async def calculate_monthly_revenue(
    property_id: str,
    month: int,
    year: int,
    tenant_id: str,
    db_session=None,
) -> Decimal:
    """
    Calculates revenue for a specific month in the property's local timezone.
    """
    summary = await calculate_monthly_revenue_summary(property_id, tenant_id, month, year, db_session)
    return Decimal(summary["total"])


async def calculate_monthly_revenue_summary(
    property_id: str,
    tenant_id: str,
    month: int,
    year: int,
    db_session=None,
) -> Dict[str, Any]:
    """
    Aggregates revenue for a property/tenant within a calendar month,
    using the property's local timezone for month boundaries.
    """
    try:
        from app.core.database_pool import db_pool

        await db_pool.initialize()

        if not db_pool.session_factory:
            raise Exception("Database pool not available")

        async with db_pool.get_session() as session:
            timezone_name = await _get_property_timezone(session, property_id, tenant_id)
            if timezone_name is None:
                # Property does not belong to this tenant — no access / no data.
                return _empty_revenue(property_id, tenant_id)

            start_date, end_date = _month_bounds_utc(year, month, timezone_name)

            result = await session.execute(
                text(
                    """
                    SELECT
                        COALESCE(SUM(total_amount), 0) AS total_revenue,
                        COUNT(*) AS reservation_count
                    FROM reservations
                    WHERE property_id = :property_id
                      AND tenant_id = :tenant_id
                      AND check_in_date >= :start_date
                      AND check_in_date < :end_date
                    """
                ),
                {
                    "property_id": property_id,
                    "tenant_id": tenant_id,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
            row = result.fetchone()
            total_revenue = Decimal(str(row.total_revenue if row else 0))
            count = int(row.reservation_count if row else 0)
            return {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "total": _quantize_money(total_revenue),
                "currency": "USD",
                "count": count,
            }

    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}) monthly: {e}")
        # Do not invent cross-tenant mock figures on failure.
        return _empty_revenue(property_id, tenant_id)


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates all revenue for a property scoped to the authenticated tenant.
    """
    try:
        from app.core.database_pool import db_pool

        await db_pool.initialize()

        if not db_pool.session_factory:
            raise Exception("Database pool not available")

        async with db_pool.get_session() as session:
            # Ensure the property belongs to this tenant before aggregating.
            timezone_name = await _get_property_timezone(session, property_id, tenant_id)
            if timezone_name is None:
                return _empty_revenue(property_id, tenant_id)

            result = await session.execute(
                text(
                    """
                    SELECT
                        COALESCE(SUM(total_amount), 0) AS total_revenue,
                        COUNT(*) AS reservation_count
                    FROM reservations
                    WHERE property_id = :property_id AND tenant_id = :tenant_id
                    """
                ),
                {"property_id": property_id, "tenant_id": tenant_id},
            )
            row = result.fetchone()
            total_revenue = Decimal(str(row.total_revenue if row else 0))
            count = int(row.reservation_count if row else 0)
            return {
                "property_id": property_id,
                "tenant_id": tenant_id,
                "total": _quantize_money(total_revenue),
                "currency": "USD",
                "count": count,
            }

    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}): {e}")
        # Fail closed: never return another tenant's figures from a shared mock map.
        return _empty_revenue(property_id, tenant_id)
