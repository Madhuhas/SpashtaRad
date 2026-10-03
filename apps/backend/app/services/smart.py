"""SMART App Launch (OAuth 2.0 authorisation code + PKCE) helpers.

The viewer is registered as a *public* client: no client secret is held, and
PKCE (S256) protects the code exchange.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

_OAUTH_URIS_EXTENSION = "http://fhir-registry.smarthealthit.org/StructureDefinition/oauth-uris"


class SmartError(Exception):
    """The EHR's authorisation or FHIR server misbehaved or refused us."""


@dataclass(frozen=True)
class SmartEndpoints:
    authorization_endpoint: str
    token_endpoint: str


def normalise_issuer(issuer: str) -> str:
    return issuer.strip().rstrip("/")


def is_allowed_issuer(issuer: str, allowed: list[str]) -> bool:
    return normalise_issuer(issuer) in {normalise_issuer(item) for item in allowed}


def new_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for the S256 method."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def discover(http: httpx.AsyncClient, issuer: str) -> SmartEndpoints:
    """Find the OAuth endpoints for a FHIR server.

    Tries ``.well-known/smart-configuration`` first, then falls back to the
    ``oauth-uris`` extension in the CapabilityStatement (older servers).
    """
    base = normalise_issuer(issuer)
    try:
        response = await http.get(
            f"{base}/.well-known/smart-configuration", headers={"Accept": "application/json"}
        )
        if response.status_code == 200:
            config = response.json()
            return SmartEndpoints(config["authorization_endpoint"], config["token_endpoint"])

        response = await http.get(f"{base}/metadata", headers={"Accept": "application/fhir+json"})
        response.raise_for_status()
        endpoints: dict[str, str] = {}
        for rest in response.json().get("rest", []):
            for extension in rest.get("security", {}).get("extension", []):
                if extension.get("url") == _OAUTH_URIS_EXTENSION:
                    for item in extension.get("extension", []):
                        endpoints[item.get("url")] = item.get("valueUri")
        return SmartEndpoints(endpoints["authorize"], endpoints["token"])
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise SmartError(f"SMART discovery failed for {base}") from exc


def build_authorize_url(
    endpoints: SmartEndpoints,
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    issuer: str,
    code_challenge: str,
    launch: str | None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "aud": normalise_issuer(issuer),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if launch:
        params["launch"] = launch
    separator = "&" if "?" in endpoints.authorization_endpoint else "?"
    return f"{endpoints.authorization_endpoint}{separator}{urlencode(params)}"


async def exchange_code(
    http: httpx.AsyncClient,
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
) -> dict[str, Any]:
    try:
        response = await http.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "code_verifier": code_verifier,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        token = response.json()
        if "access_token" not in token:
            raise KeyError("access_token")
        return token
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise SmartError("Token exchange with the EHR failed") from exc


async def fetch_fhir(
    http: httpx.AsyncClient, issuer: str, path: str, access_token: str
) -> dict[str, Any]:
    try:
        response = await http.get(
            f"{normalise_issuer(issuer)}/{path.lstrip('/')}",
            headers={"Accept": "application/fhir+json", "Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SmartError(f"FHIR request failed: {path.split('?')[0]}") from exc


def summarise_patient(patient: dict[str, Any]) -> dict[str, Any]:
    """Flatten a FHIR R4 Patient into the demographics the viewer displays."""
    names = patient.get("name", [])
    name = next((n for n in names if n.get("use") == "official"), names[0] if names else {})
    display = name.get("text") or ", ".join(
        part for part in (name.get("family", ""), " ".join(name.get("given", []))) if part
    )

    identifiers = []
    mrn = None
    for identifier in patient.get("identifier", []):
        value = identifier.get("value")
        if not value:
            continue
        codes = {c.get("code") for c in identifier.get("type", {}).get("coding", [])}
        identifiers.append(
            {"system": identifier.get("system"), "value": value, "is_mrn": "MR" in codes}
        )
        if "MR" in codes and mrn is None:
            mrn = value

    return {
        "fhir_id": patient.get("id"),
        "name": display or "Unknown",
        "birth_date": patient.get("birthDate"),
        "gender": patient.get("gender"),
        "mrn": mrn,
        "identifiers": identifiers,
    }


def summarise_encounters(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    encounters = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") != "Encounter":
            continue
        types = resource.get("type", [])
        encounters.append(
            {
                "id": resource.get("id"),
                "status": resource.get("status"),
                "class": resource.get("class", {}).get("code"),
                "type": types[0].get("text") if types else None,
                "start": resource.get("period", {}).get("start"),
                "end": resource.get("period", {}).get("end"),
            }
        )
    return encounters
