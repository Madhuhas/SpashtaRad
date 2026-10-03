"""Test fixtures: synthetic DICOM, an in-memory fake PACS, a fake EHR and a fake AI engine.

Nothing here touches the network or loads a model.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from spashta_ai import AnalysisResult, Finding, RoutingDecision
from spashta_ai import anatomy as anatomy_module

from app.core.config import Settings
from app.main import create_app
from app.services.dicomweb import encode_multipart_related, parse_multipart_related

_DX_IMAGE_STORAGE = "1.2.840.10008.5.1.4.1.1.1.1"
EHR_ISSUER = "https://ehr.test/fhir"
PATIENT_ID = "SYN-0001"


def make_dataset(**overrides: object) -> Dataset:
    """A small synthetic chest radiograph carrying representative PHI."""
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = _DX_IMAGE_STORAGE
    meta.MediaStorageSOPInstanceUID = generate_uid()

    ds = Dataset()
    ds.file_meta = meta
    ds.SOPClassUID = _DX_IMAGE_STORAGE
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientName = "DOE^JANE"
    ds.PatientID = PATIENT_ID
    ds.PatientBirthDate = "19300215"
    ds.PatientSex = "F"
    ds.PatientAge = "094Y"
    ds.PatientAddress = "1 Example Street"
    ds.StudyDate = "20240315"
    ds.StudyTime = "101500"
    ds.AccessionNumber = "ACC123"
    ds.InstitutionName = "Example General Hospital"
    ds.ReferringPhysicianName = "SMITH^JOHN"
    ds.StudyDescription = "XR CHEST PA"
    ds.Modality = "DX"
    ds.BodyPartExamined = "CHEST"
    ds.Rows, ds.Columns = 64, 96
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 12
    ds.HighBit = 11
    ds.PixelRepresentation = 0
    pixels = np.full((64, 96), 100, dtype=np.uint16)
    pixels[16:48, 24:72] = 3000
    ds.PixelData = pixels.tobytes()
    for keyword, value in overrides.items():
        setattr(ds, keyword, value)
    return ds


def to_bytes(ds: Dataset) -> bytes:
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=True)
    return buffer.getvalue()


def _streamed(status: int, body: bytes = b"", content_type: str = "text/plain") -> httpx.Response:
    """An unread, streamable response - what a real network transport returns.

    ``httpx.Response(content=...)`` is pre-consumed and cannot be re-streamed,
    which would make the gateway's streaming path untestable.
    """
    return httpx.Response(
        status,
        stream=httpx.ByteStream(body),
        headers={"content-type": content_type, "content-length": str(len(body))},
    )


class FakePacs:
    """Minimal DICOMweb server backed by a dict, exposed as an httpx transport."""

    def __init__(self) -> None:
        self.instances: dict[str, bytes] = {}  # SOPInstanceUID -> Part 10 bytes
        self.requests: list[httpx.Request] = []

    def add(self, ds: Dataset) -> None:
        self.instances[str(ds.SOPInstanceUID)] = to_bytes(ds)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers.get("authorization", "").startswith("Basic ")
        segments = request.url.path.removeprefix("/dicom-web/").split("/")

        if request.method == "POST" and segments == ["studies"]:
            import pydicom

            for part in parse_multipart_related(request.content, request.headers["content-type"]):
                parsed = pydicom.dcmread(io.BytesIO(part))
                self.instances[str(parsed.SOPInstanceUID)] = part
            return _streamed(200, b"{}", "application/dicom+json")

        if request.method == "GET" and segments == ["studies"]:
            body = [
                {
                    "0020000D": {"vr": "UI", "Value": ["1.2.3"]},
                    "00080020": {"vr": "DA", "Value": ["20240315"]},
                }
            ]
            return _streamed(200, json.dumps(body).encode(), "application/dicom+json")

        if request.method == "GET" and len(segments) == 6 and segments[4] == "instances":
            data = self.instances.get(segments[5])
            if data is None:
                return _streamed(404)
            body, content_type = encode_multipart_related([data])
            return _streamed(200, body, content_type)

        return _streamed(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeEngine:
    """Stands in for the AI engine; records the datasets it is handed."""

    def __init__(self) -> None:
        self.received: list[Dataset] = []

    def status(self) -> dict:
        return {"device": "cpu", "pipelines": {"fake": {"available": True, "loaded": True}}}

    def route(self, ds: Dataset) -> RoutingDecision:
        return anatomy_module.route(ds)

    def analyze(self, ds, *, explain=True, preliminary=None) -> AnalysisResult:
        self.received.append(ds)
        heatmap = np.zeros((7, 7), dtype=np.float32)
        heatmap[3, 3] = 1.0
        return AnalysisResult(
            routing=preliminary or anatomy_module.route(ds),
            pipeline="fake",
            model_name="fake-model",
            model_version="0.0",
            findings=[
                Finding("Cardiomegaly", 0.91, True, True, heatmap if explain else None),
                Finding("Effusion", 0.12, False, True),
            ],
            heatmap_roi=(0.0, 0.0, 1.0, 1.0),
            image_shape=(int(ds.Rows), int(ds.Columns)),
        )


def fake_ehr(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == f"{EHR_ISSUER}/.well-known/smart-configuration":
        return httpx.Response(
            200,
            json={
                "authorization_endpoint": "https://ehr.test/authorize",
                "token_endpoint": "https://ehr.test/token",
            },
        )
    if url == "https://ehr.test/token":
        form = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
        if form.get("code") != "good-code" or not form.get("code_verifier"):
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200, json={"access_token": "tok", "patient": "pat-1", "expires_in": 3600}
        )
    if request.headers.get("authorization") != "Bearer tok":
        return httpx.Response(401)
    if url == f"{EHR_ISSUER}/Patient/pat-1":
        return httpx.Response(
            200,
            json={
                "resourceType": "Patient",
                "id": "pat-1",
                "name": [{"use": "official", "family": "Doe", "given": ["Jane"]}],
                "birthDate": "1930-02-15",
                "gender": "female",
                "identifier": [
                    {"system": "urn:ssn", "value": "000-00-0000"},
                    {
                        "type": {"coding": [{"code": "MR"}]},
                        "system": "urn:mrn",
                        "value": PATIENT_ID,
                    },
                ],
            },
        )
    if url.startswith(f"{EHR_ISSUER}/Encounter"):
        return httpx.Response(200, content=json.dumps({"resourceType": "Bundle", "entry": []}))
    return httpx.Response(404)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        data_dir=tmp_path,
        dicomweb_url="http://pacs.test/dicom-web",
        smart_allowed_issuers=[EHR_ISSUER],
        deid_uid_salt="unit-test-salt",
    )


@pytest.fixture
def pacs() -> FakePacs:
    return FakePacs()


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def client(settings, pacs, engine) -> Iterator[TestClient]:
    app = create_app(
        settings,
        engine=engine,
        pacs_transport=pacs.transport,
        external_transport=httpx.MockTransport(fake_ehr),
    )
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
