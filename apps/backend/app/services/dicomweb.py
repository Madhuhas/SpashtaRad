"""DICOMweb client for the PACS / VNA (QIDO-RS, WADO-RS, STOW-RS).

Vendor-neutral: only standard DICOMweb (PS3.18) requests are issued, so
Orthanc can be swapped for dcm4chee, Google Healthcare API, etc. by changing
``SPASHTA_DICOMWEB_URL``.
"""

from __future__ import annotations

import email.message
import re
import uuid
from typing import Any

import httpx

_UID_PATTERN = re.compile(r"^[0-9]+(\.[0-9]+)*$")
DICOM_JSON = "application/dicom+json"


class PacsError(Exception):
    """The PACS was unreachable or returned an error status."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def validate_uid(value: str) -> str:
    """Reject anything that is not a DICOM UID before placing it in a URL path."""
    if len(value) > 64 or not _UID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid DICOM UID: {value!r}")
    return value


def parse_multipart_related(body: bytes, content_type: str) -> list[bytes]:
    """Split a ``multipart/related`` body into its part payloads."""
    header = email.message.Message()
    header["content-type"] = content_type
    boundary = header.get_param("boundary")
    if header.get_content_maintype() != "multipart" or not isinstance(boundary, str):
        raise ValueError(f"Not a multipart response: {content_type!r}")

    delimiter = b"--" + boundary.encode("ascii")
    parts: list[bytes] = []
    # Segment 0 is the preamble; the final segment starts with the closing "--".
    for segment in body.split(delimiter)[1:]:
        if segment.startswith(b"--"):
            break
        _, separator, payload = segment.partition(b"\r\n\r\n")
        if not separator:
            raise ValueError("Malformed multipart part: missing header terminator")
        # The CRLF preceding the next delimiter belongs to the delimiter.
        parts.append(payload.removesuffix(b"\r\n"))
    return parts


def encode_multipart_related(
    parts: list[bytes], part_content_type: str = "application/dicom"
) -> tuple[bytes, str]:
    """Build a ``multipart/related`` body. Returns ``(body, content_type_header)``."""
    boundary = f"spashtarad-{uuid.uuid4().hex}"
    delimiter = b"--" + boundary.encode("ascii")
    chunks: list[bytes] = []
    for part in parts:
        chunks += [
            delimiter,
            b"\r\nContent-Type: ",
            part_content_type.encode("ascii"),
            b"\r\n\r\n",
            part,
            b"\r\n",
        ]
    chunks += [delimiter, b"--\r\n"]
    content_type = f'multipart/related; type="{part_content_type}"; boundary={boundary}'
    return b"".join(chunks), content_type


class DicomWebClient:
    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        auth = httpx.BasicAuth(username, password or "") if username else None
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", auth=auth, timeout=timeout, transport=transport
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------ QIDO-RS

    async def search_studies(self, params: dict[str, str]) -> list[dict[str, Any]]:
        response = await self._request("GET", "studies", params=params, accept=DICOM_JSON)
        # QIDO-RS answers 204 No Content when nothing matches.
        return response.json() if response.status_code == 200 and response.content else []

    # ------------------------------------------------------------------ WADO-RS

    async def retrieve_instance(self, study_uid: str, series_uid: str, sop_uid: str) -> bytes:
        """Fetch one instance as a DICOM Part 10 byte string."""
        path = (
            f"studies/{validate_uid(study_uid)}/series/{validate_uid(series_uid)}"
            f"/instances/{validate_uid(sop_uid)}"
        )
        response = await self._request(
            "GET", path, accept='multipart/related; type="application/dicom"; transfer-syntax=*'
        )
        parts = parse_multipart_related(response.content, response.headers.get("content-type", ""))
        if not parts:
            raise PacsError("PACS returned an empty multipart response", 502)
        return parts[0]

    # ------------------------------------------------------------------ STOW-RS

    async def store_instances(self, instances: list[bytes]) -> dict[str, Any]:
        body, content_type = encode_multipart_related(instances)
        response = await self._request(
            "POST", "studies", content=body, content_type=content_type, accept=DICOM_JSON
        )
        return response.json() if response.content else {}

    # -------------------------------------------------------------------- proxy

    async def forward(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        headers: dict[str, str] | None = None,
        content: Any = None,
    ) -> httpx.Response:
        """Send a request and return the *streaming* response.

        The caller owns the response and must ``aclose()`` it.
        """
        request = self._http.build_request(
            method, path, params=params, headers=headers, content=content
        )
        try:
            return await self._http.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise PacsError(f"PACS unreachable: {type(exc).__name__}") from exc

    async def ping(self) -> bool:
        try:
            await self._request("GET", "studies", params={"limit": "1"}, accept=DICOM_JSON)
        except PacsError:
            return False
        return True

    # ----------------------------------------------------------------- internal

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        accept: str,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        headers = {"Accept": accept}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            response = await self._http.request(
                method, path, params=params, headers=headers, content=content
            )
        except httpx.HTTPError as exc:
            raise PacsError(f"PACS unreachable: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise PacsError(
                f"PACS returned HTTP {response.status_code} for {method} {path}",
                response.status_code,
            )
        return response
