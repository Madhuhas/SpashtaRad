"""Authentication of CDS Hooks callers ("Trusting CDS Clients" in the spec).

An EHR signs a short-lived JWT for every call and sends it as a bearer token.
A token is accepted only when all of these hold:

* ``iss`` is a registered CDS client (``SPASHTA_CDS_TRUSTED_CLIENTS``);
* the signature verifies against a key in the JWKS registered for that client.
  The key URL always comes from our configuration: a ``jku`` header is only
  tolerated when it names that same URL, never followed on its own;
* ``aud`` is the URL of the service being invoked;
* it is unexpired, short-lived, and its ``jti`` has not been seen before.

Replay protection is held in process memory, so it covers a single worker.
Deployments running several workers need a shared store for it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx
import jwt

from app.core.security import ALLOWED_JWT_ALGORITHMS

_LEEWAY_SECONDS = 30
# The spec recommends tokens valid for about five minutes. Anything much longer
# is a misconfigured client, and would also let the replay cache grow unbounded.
_MAX_LIFETIME_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600
# An unknown `kid` triggers a refetch (key rotation), but not more often than this.
_JWKS_MIN_REFETCH_SECONDS = 60


class CdsAuthError(Exception):
    """The caller did not prove it is a registered CDS client."""


def _normalise(url: str) -> str:
    return url.strip().rstrip("/")


class CdsClientVerifier:
    def __init__(
        self,
        http: httpx.AsyncClient,
        trusted_clients: dict[str, str],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._http = http
        self._jwks_urls = {_normalise(iss): url for iss, url in trusted_clients.items()}
        self._clock = clock
        self._keys: dict[str, tuple[float, dict[str | None, Any]]] = {}  # JWKS URL -> keys
        self._fetch_lock = asyncio.Lock()
        self._seen: dict[tuple[str, str], float] = {}  # (iss, jti) -> expiry

    async def verify(self, token: str, *, audiences: list[str]) -> str:
        """Validate a CDS client's JWT and return its issuer."""
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as exc:
            raise CdsAuthError("Malformed JWT") from exc

        issuer = unverified.get("iss")
        jwks_url = self._jwks_urls.get(_normalise(issuer)) if isinstance(issuer, str) else None
        if jwks_url is None:
            raise CdsAuthError("JWT issuer is not a registered CDS client")
        if header.get("alg") not in ALLOWED_JWT_ALGORITHMS:
            raise CdsAuthError("JWT signing algorithm is not accepted")
        if header.get("jku") not in (None, jwks_url):
            raise CdsAuthError("JWT 'jku' does not match the JWKS registered for this client")

        key = await self._signing_key(jwks_url, header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=ALLOWED_JWT_ALGORITHMS,
                audience=audiences,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["iss", "aud", "exp", "iat", "jti"]},
            )
        except jwt.PyJWTError as exc:
            raise CdsAuthError(f"JWT rejected: {exc}") from exc

        now = self._clock()
        expires = float(claims["exp"])
        if expires - now > _MAX_LIFETIME_SECONDS:
            raise CdsAuthError("JWT lifetime is too long")
        self._reject_replay(issuer, str(claims["jti"]), expires, now)
        return issuer

    def _reject_replay(self, issuer: str, jti: str, expires: float, now: float) -> None:
        for seen_key in [k for k, exp in self._seen.items() if exp + _LEEWAY_SECONDS < now]:
            del self._seen[seen_key]
        if (issuer, jti) in self._seen:
            raise CdsAuthError("JWT has already been used")
        self._seen[(issuer, jti)] = expires

    async def _signing_key(self, jwks_url: str, kid: str | None) -> Any:
        async with self._fetch_lock:
            fetched_at, keys = self._keys.get(jwks_url, (float("-inf"), {}))
            age = self._clock() - fetched_at
            stale = age > _JWKS_TTL_SECONDS
            rotated = self._pick(keys, kid) is None and age > _JWKS_MIN_REFETCH_SECONDS
            if stale or rotated:
                keys = await self._fetch_keys(jwks_url)
                self._keys[jwks_url] = (self._clock(), keys)

        key = self._pick(keys, kid)
        if key is None:
            raise CdsAuthError("JWT signing key is not in the client's registered JWKS")
        return key

    @staticmethod
    def _pick(keys: dict[str | None, Any], kid: str | None) -> Any | None:
        # A token without `kid` is only unambiguous when the client publishes one key.
        if kid is None and len(keys) == 1:
            return next(iter(keys.values()))
        return keys.get(kid)

    async def _fetch_keys(self, jwks_url: str) -> dict[str | None, Any]:
        try:
            response = await self._http.get(jwks_url, headers={"Accept": "application/json"})
            response.raise_for_status()
            key_set = jwt.PyJWKSet.from_dict(response.json())
        except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError) as exc:
            raise CdsAuthError("Could not load the CDS client's JWKS") from exc
        return {key.key_id: key.key for key in key_set.keys}
