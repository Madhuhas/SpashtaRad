"""Authentication and role-based authorisation.

Two modes:

* ``dev``  - every request is a local developer with all roles. Refused when
  ``SPASHTA_ENVIRONMENT=production`` (see ``Settings``).
* ``oidc`` - requests must carry a bearer JWT issued by the configured identity
  provider (Keycloak, Entra ID, Okta, ...). Signature, issuer, audience and
  expiry are verified against the provider's JWKS.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.core.config import Settings

ROLE_RADIOLOGIST = "radiologist"
ROLE_ADMIN = "admin"

# Asymmetric algorithms only: accepting HS* with a JWKS-based verifier enables
# key-confusion attacks.
ALLOWED_JWT_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


@dataclass(frozen=True)
class Principal:
    subject: str
    name: str
    roles: frozenset[str] = field(default_factory=frozenset)


_DEV_PRINCIPAL = Principal(
    subject="dev-user", name="Local Developer", roles=frozenset({ROLE_RADIOLOGIST, ROLE_ADMIN})
)


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url, cache_keys=True)


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


def _verify_bearer(token: str, settings: Settings) -> Principal:
    if not (settings.oidc_jwks_url and settings.oidc_issuer and settings.oidc_audience):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "OIDC authentication is not configured"
        )
    try:
        signing_key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALLOWED_JWT_ALGORITHMS,
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise _unauthorised("Invalid or expired access token") from exc

    roles = claims.get(settings.oidc_roles_claim, [])
    if isinstance(roles, str):
        roles = roles.split()
    return Principal(
        subject=str(claims["sub"]),
        name=str(claims.get("name") or claims.get("preferred_username") or claims["sub"]),
        roles=frozenset(str(role) for role in roles),
    )


def get_principal(request: Request) -> Principal:
    """FastAPI dependency: the authenticated caller."""
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return _DEV_PRINCIPAL

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorised("Bearer token required")
    return _verify_bearer(token, settings)


def require_role(role: str):
    """Dependency factory: reject callers that lack ``role``."""

    def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if role not in principal.roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{role}' required")
        return principal

    return dependency
