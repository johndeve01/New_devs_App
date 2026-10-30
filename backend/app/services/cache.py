import json
import os
from typing import Any, Dict, Optional

import redis.asyncio as redis

# Initialize Redis client (typically configured centrally).
redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


def _revenue_cache_key(tenant_id: str, property_id: str, month: Optional[int], year: Optional[int]) -> str:
    """Cache keys must include tenant (and period) to prevent cross-tenant leakage."""
    if month is not None and year is not None:
        return f"revenue:{tenant_id}:{property_id}:{year}:{month:02d}"
    return f"revenue:{tenant_id}:{property_id}"


async def get_revenue_summary(
    property_id: str,
    tenant_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Fetches revenue summary, utilizing caching to improve performance.
    """
    cache_key = _revenue_cache_key(tenant_id, property_id, month, year)

    # Try to get from cache
    cached = await redis_client.get(cache_key)
    if cached:
        return json.loads(cached)

    # Revenue calculation is delegated to the reservation service.
    from app.services.reservations import calculate_monthly_revenue_summary, calculate_total_revenue

    if month is not None and year is not None:
        result = await calculate_monthly_revenue_summary(property_id, tenant_id, month, year)
    else:
        result = await calculate_total_revenue(property_id, tenant_id)

    # Cache the result for 5 minutes
    await redis_client.setex(cache_key, 300, json.dumps(result))

    return result
