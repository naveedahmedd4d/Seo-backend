from functools import wraps
from typing import Callable
from fastapi import HTTPException, status
from collections import defaultdict
from datetime import datetime, timedelta
import time

# Simple in-memory rate limiter (use Redis in production)
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def rate_limit(max_requests: int = 10, window_seconds: int = 60):
    """Rate limiting decorator"""
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Get client identifier (IP or user ID)
            request = kwargs.get('request') or args[0] if args else None
            if request:
                client_id = request.client.host if hasattr(request, 'client') else 'default'
            else:
                client_id = 'default'
            
            # Clean old requests
            now = time.time()
            _rate_limit_store[client_id] = [
                req_time for req_time in _rate_limit_store[client_id]
                if now - req_time < window_seconds
            ]
            
            # Check rate limit
            if len(_rate_limit_store[client_id]) >= max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded: {max_requests} requests per {window_seconds} seconds"
                )
            
            # Record request
            _rate_limit_store[client_id].append(now)
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

