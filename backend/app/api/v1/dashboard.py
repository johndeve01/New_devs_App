from decimal import Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import authenticate_request as get_current_user
from app.services.cache import get_revenue_summary

router = APIRouter()


@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = Query(default=3, ge=1, le=12),
    year: Optional[int] = Query(default=2024, ge=2000, le=2100),
    current_user=Depends(get_current_user),
) -> Dict[str, Any]:
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Authenticated user missing tenant context")

    revenue_data = await get_revenue_summary(property_id, tenant_id, month=month, year=year)

    # Keep money as a Decimal-serialized string to avoid float precision drift.
    total = revenue_data["total"]
    if not isinstance(total, str):
        total = str(Decimal(str(total)))

    return {
        "property_id": revenue_data["property_id"],
        "total_revenue": total,
        "currency": revenue_data["currency"],
        "reservations_count": revenue_data["count"],
        "tenant_id": revenue_data.get("tenant_id", tenant_id),
        "month": month,
        "year": year,
    }
