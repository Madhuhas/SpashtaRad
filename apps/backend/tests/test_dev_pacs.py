"""The development DICOMweb server speaks enough DICOMweb for the backend's own client."""

from __future__ import annotations

import io

import pydicom
import pytest
from fastapi.testclient import TestClient

from app.services.dicomweb import encode_multipart_related, parse_multipart_related
from tests.conftest import make_dataset, to_bytes
from tools.dev_pacs import Store, create_app

AUTH = ("user", "secret")


@pytest.fixture
def pacs_client() -> TestClient:
    return TestClient(create_app(Store(), *AUTH))


def test_dev_pacs_requires_its_login(pacs_client):
    assert pacs_client.get("/dicom-web/studies").status_code == 401
    assert pacs_client.get("/dicom-web/studies", auth=("user", "wrong")).status_code == 401
    assert pacs_client.get("/dicom-web/studies", auth=AUTH).status_code == 204  # empty


def test_dev_pacs_store_search_and_retrieve(pacs_client):
    ds = make_dataset()
    body, content_type = encode_multipart_related([to_bytes(ds)])
    stored = pacs_client.post(
        "/dicom-web/studies", content=body, headers={"content-type": content_type}, auth=AUTH
    )
    assert stored.status_code == 200, stored.text

    search = pacs_client.get("/dicom-web/studies", params={"PatientName": "*doe*"}, auth=AUTH)
    (study,) = search.json()
    assert study["0020000D"]["Value"] == [ds.StudyInstanceUID]
    assert study["00080061"]["Value"] == ["DX"]
    assert pacs_client.get("/dicom-web/studies?PatientID=nobody", auth=AUTH).status_code == 204

    base = f"/dicom-web/studies/{ds.StudyInstanceUID}"
    (series,) = pacs_client.get(f"{base}/series", auth=AUTH).json()
    assert series["0020000E"]["Value"] == [ds.SeriesInstanceUID]

    series_path = f"{base}/series/{ds.SeriesInstanceUID}"
    (metadata,) = pacs_client.get(f"{series_path}/metadata", auth=AUTH).json()
    assert metadata["00280010"]["Value"] == [64] and "7FE00010" not in metadata

    instance_path = f"{series_path}/instances/{ds.SOPInstanceUID}"
    instance = pacs_client.get(instance_path, auth=AUTH)
    (part,) = parse_multipart_related(instance.content, instance.headers["content-type"])
    assert pydicom.dcmread(io.BytesIO(part)).SOPInstanceUID == ds.SOPInstanceUID

    frame = pacs_client.get(f"{instance_path}/frames/1", auth=AUTH)
    assert 'type="application/octet-stream"' in frame.headers["content-type"]
    (pixels,) = parse_multipart_related(frame.content, frame.headers["content-type"])
    assert pixels == ds.PixelData
    assert pacs_client.get(f"{instance_path}/frames/2", auth=AUTH).status_code == 404


def test_dev_pacs_rejects_non_dicom_uploads(pacs_client):
    body, content_type = encode_multipart_related([b"not dicom"])
    response = pacs_client.post(
        "/dicom-web/studies", content=body, headers={"content-type": content_type}, auth=AUTH
    )
    assert response.status_code == 400
