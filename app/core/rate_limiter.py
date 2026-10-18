from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi import Request
from fastapi.responses import JSONResponse

limiter = Limiter(key_func=get_remote_address, default_limits=[])


def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return uniform JSON error for rate limit violations."""
    return JSONResponse(
        status_code=429,
        content={
            "status": "error",
            "error": "Too many requests. Please try again later.",
            "detail": exc.detail,
        },
    )
