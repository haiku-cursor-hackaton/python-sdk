"""Optional API-key gating for the UCP operation routers.

In the Genko model, buyers and vendors are issued keys by the Genko gateway, and
stores are reached *through* that gateway rather than the open internet. A store
can therefore require callers to present a shared secret (the vendor's
Genko-issued key) on the REST and MCP transports, so only the trusted gateway
can search, check out, or place orders. Discovery (``/.well-known/ucp``) is left
open so the gateway can still read the profile.

When no keys are configured the dependency is absent and the surface stays open.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


def normalize_api_keys(api_keys: str | Sequence[str] | None) -> tuple[str, ...]:
    """Coerce a string / iterable / None into a tuple of non-empty keys."""
    if api_keys is None:
        return ()
    raw = [api_keys] if isinstance(api_keys, str) else list(api_keys)
    return tuple(key.strip() for key in raw if key and key.strip())


def build_api_key_dependency(api_keys: Sequence[str]) -> Callable[..., None]:
    """Build a FastAPI dependency that requires a Bearer token in ``api_keys``.

    Uses a constant-time comparison to avoid leaking key material via timing.
    Raises HTTP 401 when the token is missing or unrecognized.
    """
    valid = tuple(api_keys)
    bearer = HTTPBearer(auto_error=False, description="Genko gateway API key")

    def verify(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    ) -> None:
        token = credentials.credentials if credentials else ""
        if not any(secrets.compare_digest(token, key) for key in valid):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing API key.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify
