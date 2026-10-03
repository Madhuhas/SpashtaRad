"""A small in-memory DICOMweb server for development without Docker.

Stands in for Orthanc on the same URL and credentials, so the backend needs no
configuration to use it::

    python tools/dev_pacs.py ../../samples

It implements just the DICOMweb the viewer and backend use: QIDO-RS study and
series search, WADO-RS metadata, instance and frame retrieval, and STOW-RS.
Everything is held in memory and lost on exit. It is a development aid, not a
PACS: no persistence, no DIMSE, no transcoding, and one fixed login.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import secrets
import sys
from pathlib import Path
from typing import Any

import pydicom
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydicom.dataset import Dataset
from pydicom.encaps import get_frame

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.dicomweb import (  # noqa: E402 - needs the path set up above
    DICOM_JSON,
    encode_multipart_related,
    parse_multipart_related,
)

_STUDY_FIELDS = (
    "StudyInstanceUID", "StudyDate", "StudyTime", "AccessionNumber", "StudyDescription",
    "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
)  # fmt: skip
_SERIES_FIELDS = ("SeriesInstanceUID", "SeriesNumber", "SeriesDescription", "Modality")
# Media types for single frames of the compressed transfer syntaxes (PS3.18 Table 8.7.3-5).
_FRAME_MEDIA_TYPES = {
    "1.2.840.10008.1.2.4.50": "image/jpeg",
    "1.2.840.10008.1.2.4.51": "image/jpeg",
    "1.2.840.10008.1.2.4.57": "image/jpeg",
    "1.2.840.10008.1.2.4.70": "image/jpeg",
    "1.2.840.10008.1.2.4.80": "image/jls",
    "1.2.840.10008.1.2.4.81": "image/jls",
    "1.2.840.10008.1.2.4.90": "image/jp2",
    "1.2.840.10008.1.2.4.91": "image/jp2",
    "1.2.840.10008.1.2.4.201": "image/jphc",
    "1.2.840.10008.1.2.4.202": "image/jphc",
    "1.2.840.10008.1.2.4.203": "image/jphc",
    "1.2.840.10008.1.2.5": "image/dicom-rle",
}


class Store:
    """SOPInstanceUID -> (Part 10 bytes, header without pixel data)."""

    def __init__(self) -> None:
        self.instances: dict[str, tuple[bytes, Dataset]] = {}

    def add(self, data: bytes) -> Dataset:
        header = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)
        for keyword in ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"):
            if not header.get(keyword):
                raise ValueError(f"DICOM object is missing {keyword}")
        self.instances[str(header.SOPInstanceUID)] = (data, header)
        return header

    def headers(self, study: str | None = None, series: str | None = None) -> list[Dataset]:
        return [
            header
            for _, header in self.instances.values()
            if (study is None or header.StudyInstanceUID == study)
            and (series is None or header.SeriesInstanceUID == series)
        ]

    def get(self, study: str, series: str, sop: str) -> tuple[bytes, Dataset]:
        entry = self.instances.get(sop)
        if (
            entry is None
            or entry[1].StudyInstanceUID != study
            or entry[1].SeriesInstanceUID != series
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such instance")
        return entry


def _summary(source: Dataset, fields: tuple[str, ...], **extra: Any) -> dict[str, Any]:
    summary = Dataset()
    for keyword in fields:
        if keyword in source:
            summary[keyword] = source[keyword]
    for keyword, value in extra.items():
        setattr(summary, keyword, value)
    return summary.to_json_dict()


def _matches(header: Dataset, query: dict[str, str]) -> bool:
    """QIDO-RS matching on the attributes the viewer searches by (wildcards allowed)."""
    for keyword in ("PatientName", "PatientID", "StudyInstanceUID", "AccessionNumber"):
        pattern = query.get(keyword)
        if pattern and not fnmatch.fnmatchcase(
            str(header.get(keyword, "")).upper(), pattern.upper()
        ):
            return False
    return True


def _json(items: list[dict[str, Any]]) -> Response:
    if not items:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return Response(json.dumps(items), media_type=DICOM_JSON)


def create_app(store: Store, username: str, password: str) -> FastAPI:
    basic = HTTPBasic()

    def authenticate(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
        user_ok = secrets.compare_digest(credentials.username, username)
        password_ok = secrets.compare_digest(credentials.password, password)
        if not (user_ok and password_ok):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"}
            )

    app = FastAPI(title="SpashtaRad dev PACS", dependencies=[Depends(authenticate)])
    root = "/dicom-web"

    @app.get(f"{root}/studies")
    def search_studies(request: Request) -> Response:
        query = dict(request.query_params)
        studies: dict[str, list[Dataset]] = {}
        for header in store.headers():
            if _matches(header, query):
                studies.setdefault(str(header.StudyInstanceUID), []).append(header)
        limit = int(query.get("limit", 0)) or len(studies)
        return _json(
            [
                _summary(
                    headers[0],
                    _STUDY_FIELDS,
                    ModalitiesInStudy=sorted({str(h.get("Modality", "")) for h in headers}),
                    NumberOfStudyRelatedInstances=len(headers),
                )
                for headers in list(studies.values())[:limit]
            ]
        )

    @app.get(f"{root}/studies/{{study}}/series")
    def search_series(study: str) -> Response:
        series: dict[str, list[Dataset]] = {}
        for header in store.headers(study):
            series.setdefault(str(header.SeriesInstanceUID), []).append(header)
        return _json(
            [
                _summary(
                    headers[0],
                    ("StudyInstanceUID", *_SERIES_FIELDS),
                    NumberOfSeriesRelatedInstances=len(headers),
                )
                for headers in series.values()
            ]
        )

    @app.get(f"{root}/studies/{{study}}/series/{{series}}/metadata")
    def series_metadata(study: str, series: str) -> Response:
        headers = store.headers(study, series)
        if not headers:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such series")
        # Large values (lookup tables, overlays) are left out rather than served as bulk data.
        return _json(
            [
                header.to_json_dict(bulk_data_threshold=4096, bulk_data_element_handler=None)
                for header in headers
            ]
        )

    @app.get(f"{root}/studies/{{study}}/series/{{series}}/instances/{{sop}}")
    def retrieve_instance(study: str, series: str, sop: str) -> Response:
        data, _ = store.get(study, series, sop)
        body, content_type = encode_multipart_related([data])
        return Response(body, media_type=content_type)

    @app.get(f"{root}/studies/{{study}}/series/{{series}}/instances/{{sop}}/frames/{{frame}}")
    def retrieve_frame(study: str, series: str, sop: str, frame: int) -> Response:
        data, _ = store.get(study, series, sop)
        ds = pydicom.dcmread(io.BytesIO(data))
        frames = int(ds.get("NumberOfFrames", 1) or 1)
        if not 1 <= frame <= frames:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such frame")

        syntax = ds.file_meta.TransferSyntaxUID
        if syntax.is_encapsulated:
            payload = get_frame(ds.PixelData, frame - 1, number_of_frames=frames)
            media_type = _FRAME_MEDIA_TYPES.get(str(syntax), "application/octet-stream")
        else:
            size = ds.Rows * ds.Columns * ds.SamplesPerPixel * ds.BitsAllocated // 8
            payload = ds.PixelData[(frame - 1) * size : frame * size]
            media_type = "application/octet-stream"
        body, content_type = encode_multipart_related(
            [payload], f"{media_type}; transfer-syntax={syntax}"
        )
        # The part type contains its own parameter, so it must be quoted in the header.
        boundary = content_type.rsplit("boundary=", 1)[1]
        return Response(
            body,
            media_type=f'multipart/related; type="{media_type}"; boundary={boundary}',
        )

    @app.post(f"{root}/studies")
    async def store_instances(request: Request) -> Response:
        try:
            parts = parse_multipart_related(
                await request.body(), request.headers.get("content-type", "")
            )
            for part in parts:
                header = store.add(part)
                print(f"stored {header.SOPInstanceUID} ({header.get('Modality', '?')})")
        except (ValueError, pydicom.errors.InvalidDicomError) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return Response("{}", media_type=DICOM_JSON)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="DICOM files or folders to preload")
    parser.add_argument("--port", type=int, default=8042)
    parser.add_argument("--username", default="spashtarad")
    parser.add_argument("--password", default="spashtarad")
    args = parser.parse_args()

    store = Store()
    for path in args.paths:
        for file in sorted(path.rglob("*")) if path.is_dir() else [path]:
            if not file.is_file():
                continue
            try:
                header = store.add(file.read_bytes())
            except (ValueError, pydicom.errors.InvalidDicomError, AttributeError) as exc:
                print(f"skipped {file.name}: {exc}")
                continue
            print(f"loaded {file.name}: {header.get('Modality', '?')} {header.SOPInstanceUID}")
    print(f"{len(store.instances)} instance(s) at http://127.0.0.1:{args.port}/dicom-web")

    # Loopback only: this server has one fixed login and no TLS.
    uvicorn.run(
        create_app(store, args.username, args.password),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
