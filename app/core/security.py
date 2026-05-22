from __future__ import annotations

from fastapi import Request, HTTPException, Depends

from app.core.user_registry import TierContext, user_registry

def get_device_id(request: Request) -> str | None:
    return request.headers.get("X-Device-ID") or None

def get_tier_context(
    request: Request,
    device_id: str | None = Depends(get_device_id),
) -> TierContext:
    """Determine the requester's tier context based on device ID or client IP. Device ID takes precedence if provided."""
    if device_id:
        ctx = user_registry.resolve_device(device_id)
        if ctx is not None:
            return ctx

    client_host = (request.client.host if request.client else "") or ""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    is_direct_localhost = (
        client_host in ("127.0.0.1", "::1", "localhost")
        and not forwarded_for
    )
    if is_direct_localhost:
        return TierContext.admin()

    return TierContext.for_tier("strangers")

def require_admin(tier_ctx: TierContext = Depends(get_tier_context)) -> TierContext:
    """Raise HTTP 403 if the requester is not admin tier."""
    if not tier_ctx.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return tier_ctx