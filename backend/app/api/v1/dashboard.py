from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import authenticate_request as get_current_user
from app.services.cache import get_revenue_summary
from app.services.reservations import RevenueDataAccessError

router = APIRouter()


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = Query(default=None, ge=1, le=12),
    year: Optional[int] = Query(default=None, ge=2000, le=2100),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Authenticated user missing tenant context")

    if (month is None) ^ (year is None):
        raise HTTPException(
            status_code=400,
            detail="month and year must be provided together",
        )

    try:
        revenue_data = await get_revenue_summary(property_id, tenant_id, month=month, year=year)
    except RevenueDataAccessError as exc:
        # Do not surface fake zero totals when the database is unavailable.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Keep money as a Decimal-serialized string to avoid float precision drift.
    total = revenue_data["total"]
    if not isinstance(total, str):
        total = str(Decimal(str(total)))

    payload: Dict[str, Any] = {
        "property_id": revenue_data["property_id"],
        "total_revenue": total,
        "currency": revenue_data["currency"],
        "reservations_count": revenue_data["count"],
        "tenant_id": revenue_data.get("tenant_id", tenant_id),
    }
    if month is not None and year is not None:
        payload["month"] = month
        payload["year"] = year
    return payload
