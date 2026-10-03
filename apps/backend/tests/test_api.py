"""API tests against the real app with a fake PACS, fake EHR and fake engine."""

from __future__ import annotations

import io
import sqlite3
import time
import uuid

import jwt
import pydicom
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image

from tests.conftest import (
    CDS_CLIENT,
    CDS_JWKS_URL,
    CDS_KEY_ID,
    CDS_PRIVATE_KEY,
    EHR_ISSUER,
    PATIENT_ID,
    make_dataset,
    to_bytes,
)


def _upload(ds):
    return {"file": ("image.dcm", to_bytes(ds), "application/dicom")}


def _audit_actions(client) -> list[str]:
    return [event["action"] for event in client.get("/api/v1/audit/events").json()]


# ---------------------------------------------------------------------- health


def test_health_and_readiness(client):
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz").json()
    assert ready["database"] is True and ready["pacs"] is True


def test_responses_carry_a_request_id(client):
    assert client.get("/healthz").headers["x-request-id"]
    assert (
        client.get("/healthz", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )


# -------------------------------------------------------------------------- AI


def test_analyze_upload_returns_findings_report_and_heatmap(client, engine):
    response = client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset()))
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["routing"]["region"] == "CHEST" and body["routing"]["source"] == "dicom_tags"
    top = body["findings"][0]
    assert top["label"] == "Cardiomegaly" and top["positive"] is True
    assert top["codes"]["icd10"]["code"] == "I51.7"
    assert body["findings"][1]["heatmap_url"] is None
    assert body["report"]["impression"][0] == "High-probability cardiomegaly."
    assert body["severity"] == "warning"

    heatmap = client.get(top["heatmap_url"])
    assert heatmap.status_code == 200 and heatmap.headers["content-type"] == "image/png"
    image = Image.open(io.BytesIO(heatmap.content))
    assert image.mode == "RGBA" and image.size == (512, 341)  # 96x64 image aspect

    assert client.get(f"/api/v1/ai/results/{body['id']}").json()["id"] == body["id"]
    assert client.get(f"/api/v1/ai/results/{body['id']}/heatmaps/1").status_code == 404


def test_engine_only_ever_receives_deidentified_data(client, engine):
    original = make_dataset()
    client.post("/api/v1/ai/analyze-upload", files=_upload(original))

    (received,) = engine.received
    assert received.PatientName == "ANONYMOUS"
    assert received.PatientID != PATIENT_ID
    assert received.StudyInstanceUID != original.StudyInstanceUID
    assert "InstitutionName" not in received


def test_routing_survives_deidentification_of_description(client):
    """Body part known only from StudyDescription, which de-identification removes."""
    ds = make_dataset(StudyDescription="XR WRIST LEFT")
    del ds.BodyPartExamined
    body = client.post("/api/v1/ai/analyze-upload", files=_upload(ds)).json()
    assert body["routing"]["region"] == "MSK"
    assert body["routing"]["source"] == "description_text"


def test_invalid_upload_is_rejected(client):
    response = client.post(
        "/api/v1/ai/analyze-upload", files={"file": ("x.dcm", b"not dicom", "application/dicom")}
    )
    assert response.status_code == 400


def test_analyze_pacs_instance_then_export_secondary_capture(client, pacs):
    ds = make_dataset()
    pacs.add(ds)
    uids = {
        "study_instance_uid": str(ds.StudyInstanceUID),
        "series_instance_uid": str(ds.SeriesInstanceUID),
        "sop_instance_uid": str(ds.SOPInstanceUID),
    }
    result = client.post("/api/v1/ai/analyze", json=uids)
    assert result.status_code == 201, result.text
    result_id = result.json()["id"]

    listed = client.get(
        "/api/v1/ai/results", params={"study_instance_uid": uids["study_instance_uid"]}
    )
    assert [r["id"] for r in listed.json()] == [result_id]

    exported = client.post(f"/api/v1/ai/results/{result_id}/export", json={"finding_index": 0})
    assert exported.status_code == 200, exported.text
    capture = pydicom.dcmread(io.BytesIO(pacs.instances[exported.json()["sop_instance_uid"]]))
    assert capture.SOPClassUID == "1.2.840.10008.5.1.4.1.1.7"
    assert capture.StudyInstanceUID == ds.StudyInstanceUID
    assert capture.PatientID == PATIENT_ID  # re-attached to the real patient in the PACS

    # A finding without a heatmap cannot be exported.
    missing = client.post(f"/api/v1/ai/results/{result_id}/export", json={"finding_index": 1})
    assert missing.status_code == 404


def test_analyze_unknown_pacs_instance_is_404(client):
    body = {"study_instance_uid": "1.2", "series_instance_uid": "1.3", "sop_instance_uid": "1.4"}
    assert client.post("/api/v1/ai/analyze", json=body).status_code == 404


def test_upload_result_cannot_be_exported(client):
    result_id = client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset())).json()["id"]
    response = client.post(f"/api/v1/ai/results/{result_id}/export", json={"finding_index": 0})
    assert response.status_code == 409


# ----------------------------------------------------------------------- DICOM


def test_upload_stores_in_pacs_and_reports_routing(client, pacs):
    ds = make_dataset(BodyPartExamined="KNEE")
    response = client.post("/api/v1/dicom/upload", files=_upload(ds))
    assert response.status_code == 201, response.text
    assert response.json()["routing"]["region"] == "MSK"
    assert str(ds.SOPInstanceUID) in pacs.instances


def test_deidentify_endpoint_returns_clean_file(client):
    response = client.post("/api/v1/dicom/deidentify", files=_upload(make_dataset()))
    assert response.status_code == 200
    clean = pydicom.dcmread(io.BytesIO(response.content))
    assert clean.PatientName == "ANONYMOUS"
    assert "warnings" in response.headers["x-deidentification-report"]


def test_dicomweb_proxy_forwards_qido_and_blocks_other_paths(client, pacs):
    response = client.get("/dicomweb/studies", params={"PatientID": PATIENT_ID})
    assert response.status_code == 200
    assert response.json()[0]["0020000D"]["Value"] == ["1.2.3"]
    assert pacs.requests[-1].url.params["PatientID"] == PATIENT_ID

    assert client.get("/dicomweb/system").status_code == 404
    assert client.get("/dicomweb/studies/not-a-uid/series").status_code == 400
    assert client.post("/dicomweb/instances").status_code == 405


def test_dicomweb_retrievals_are_audited_once_per_series(client, pacs):
    ds = make_dataset()
    pacs.add(ds)
    path = (
        f"/dicomweb/studies/{ds.StudyInstanceUID}/series/{ds.SeriesInstanceUID}"
        f"/instances/{ds.SOPInstanceUID}"
    )
    assert client.get(path).status_code == 200
    assert client.get(path).status_code == 200
    assert _audit_actions(client).count("dicom.retrieve") == 1


# ----------------------------------------------------------------------- audit


def test_actions_are_audited_and_chain_verifies(client):
    client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset()))
    events = client.get("/api/v1/audit/events", params={"action": "ai.analyze"}).json()
    assert len(events) == 1
    assert events[0]["patient_ref"] == PATIENT_ID and events[0]["outcome"] == "success"
    assert events[0]["detail"]["pipeline"] == "fake"

    status = client.get("/api/v1/audit/verify").json()
    assert status["valid"] is True and status["events_checked"] >= 2


def test_audit_chain_detects_tampering(client, settings):
    client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset()))
    client.get("/api/v1/audit/events")

    # Edit a historical row behind the application's back.
    with sqlite3.connect(settings.data_dir / "test.db") as connection:
        connection.execute("UPDATE audit_events SET actor = 'someone-else' WHERE id = 1")

    status = client.get("/api/v1/audit/verify").json()
    assert status == {"valid": False, "events_checked": 1, "first_invalid_id": 1}


# ------------------------------------------------------------------- CDS Hooks


def _hook_body(hook: str, **extra) -> dict:
    return {
        "hook": hook,
        "hookInstance": "d1577c69-dfbe-44ad-ba6d-3e05e953b2ea",
        "context": {"userId": "Practitioner/1", "patientId": "pat-1"},
        "prefetch": {
            "patient": {
                "resourceType": "Patient",
                "id": "pat-1",
                "identifier": [{"type": {"coding": [{"code": "MR"}]}, "value": PATIENT_ID}],
            }
        },
        **extra,
    }


def test_cds_discovery_lists_both_hooks(client):
    for path in ("/cds-services", "/api/v1/cds/cds-services"):
        services = client.get(path).json()["services"]
        assert {s["hook"] for s in services} == {"patient-view", "order-select"}
        assert services[0]["id"] == "spashtarad-imaging-alert"


def test_cds_patient_view_returns_card_for_matching_patient(client):
    client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset()))

    response = client.post(
        "/cds-services/spashtarad-imaging-alert", json=_hook_body("patient-view")
    )
    assert response.status_code == 200, response.text
    (card,) = response.json()["cards"]
    assert card["summary"] == "AI imaging findings: Cardiomegaly"
    assert card["indicator"] == "warning"
    assert card["source"]["label"] == "SpashtaRad AI"
    assert "High-probability cardiomegaly." in card["detail"]
    assert card["links"][0]["url"].startswith("http://localhost:5173/?study=")


def test_cds_returns_no_cards_for_unknown_patient_or_wrong_hook(client):
    client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset()))

    body = _hook_body("patient-view")
    body["prefetch"]["patient"]["identifier"][0]["value"] = "SOMEONE-ELSE"
    assert client.post("/cds-services/spashtarad-imaging-alert", json=body).json() == {"cards": []}

    wrong = client.post("/cds-services/spashtarad-imaging-alert", json=_hook_body("order-select"))
    assert wrong.status_code == 400
    assert client.post("/cds-services/nope", json=_hook_body("patient-view")).status_code == 404


def test_cds_ignores_fhir_server_outside_allow_list(client):
    """A caller-supplied fhirServer must not be fetched unless it is an approved EHR."""
    body = _hook_body(
        "patient-view",
        fhirServer="https://attacker.test/fhir",
        fhirAuthorization={"access_token": "tok", "token_type": "Bearer"},
    )
    del body["prefetch"]
    assert client.post("/cds-services/spashtarad-imaging-alert", json=body).json() == {"cards": []}


_CDS_PATH = "/cds-services/spashtarad-imaging-alert"
_CDS_URL = f"http://localhost:8000{_CDS_PATH}"


def _cds_token(key=CDS_PRIVATE_KEY, *, algorithm="ES384", headers=None, **claims) -> str:
    now = int(time.time())
    payload = {
        "iss": CDS_CLIENT,
        "aud": _CDS_URL,
        "iat": now,
        "exp": now + 300,
        "jti": str(uuid.uuid4()),
        **claims,
    }
    return jwt.encode(
        payload, key, algorithm=algorithm, headers={"kid": CDS_KEY_ID, **(headers or {})}
    )


def _cds_call(client, token: str | None, path: str = _CDS_PATH):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(path, json=_hook_body("patient-view"), headers=headers)


def test_cds_jwt_accepts_registered_client_and_audits_it(cds_jwt_client):
    cds_jwt_client.post("/api/v1/ai/analyze-upload", files=_upload(make_dataset()))

    response = _cds_call(cds_jwt_client, _cds_token())
    assert response.status_code == 200, response.text
    assert len(response.json()["cards"]) == 1

    events = cds_jwt_client.get("/api/v1/audit/events").json()
    hook_event = next(event for event in events if event["action"] == "cds.hook")
    assert hook_event["actor"] == CDS_CLIENT and hook_event["outcome"] == "success"

    # Both mounts are valid audiences, as is a `jku` naming the registered JWKS.
    versioned = "/api/v1/cds" + _CDS_PATH
    token = _cds_token(aud=f"http://localhost:8000{versioned}", headers={"jku": CDS_JWKS_URL})
    assert _cds_call(cds_jwt_client, token, versioned).status_code == 200
    # Discovery needs no token.
    assert cds_jwt_client.get("/cds-services").status_code == 200


def test_cds_jwt_rejects_untrusted_callers(cds_jwt_client):
    other_key = ec.generate_private_key(ec.SECP384R1())
    now = int(time.time())
    # Expected reason -> token that must be refused for that reason.
    rejected = {
        "Bearer JWT required": None,
        "Malformed JWT": "not-a-jwt",
        "not a registered CDS client": _cds_token(iss="https://attacker.test"),
        "Signature verification failed": _cds_token(other_key),
        "Audience": _cds_token(aud="https://other-service.test/cds-services/x"),
        "expired": _cds_token(iat=now - 900, exp=now - 600),
        "lifetime is too long": _cds_token(exp=now + 86400),
        "jti": _cds_token(jti=None),
        "'jku' does not match": _cds_token(headers={"jku": "https://attacker.test/jwks"}),
        "algorithm is not accepted": _cds_token("x" * 48, algorithm="HS384"),
        "not in the client's registered JWKS": _cds_token(headers={"kid": "rotated-away"}),
    }
    for reason, token in rejected.items():
        response = _cds_call(cds_jwt_client, token)
        assert response.status_code == 401, f"{reason}: {response.status_code} {response.text}"
        assert reason in response.json()["detail"]
        assert response.headers["www-authenticate"] == "Bearer"

    feedback = cds_jwt_client.post(f"{_CDS_PATH}/feedback", json={"feedback": []})
    assert feedback.status_code == 401

    events = cds_jwt_client.get("/api/v1/audit/events?limit=100").json()
    failures = [e for e in events if e["action"] == "cds.hook" and e["outcome"] == "failure"]
    assert len(failures) == len(rejected) + 1


def test_cds_jwt_cannot_be_replayed(cds_jwt_client):
    token = _cds_token()
    assert _cds_call(cds_jwt_client, token).status_code == 200
    replay = _cds_call(cds_jwt_client, token)
    assert replay.status_code == 401
    assert replay.json()["detail"] == "JWT has already been used"


# --------------------------------------------------------------- SMART on FHIR


def test_smart_launch_rejects_unlisted_issuer(client):
    response = client.get("/api/v1/fhir/launch", params={"iss": "https://attacker.test/fhir"})
    assert response.status_code == 400


def test_smart_launch_callback_and_context(client):
    alias = client.get("/fhir/launch", params={"iss": EHR_ISSUER, "launch": "xyz"})
    assert alias.status_code == 307 and alias.headers["location"].startswith("/api/v1/fhir/launch?")

    launch = client.get(alias.headers["location"])
    assert launch.status_code == 302
    authorize = launch.headers["location"]
    assert authorize.startswith("https://ehr.test/authorize?")
    assert "code_challenge_method=S256" in authorize and "launch=xyz" in authorize
    state = authorize.split("state=")[1].split("&")[0]

    assert client.get("/api/v1/fhir/context").status_code == 401  # nothing established yet

    callback = client.get("/api/v1/fhir/callback", params={"state": state, "code": "good-code"})
    assert callback.status_code == 302 and callback.headers["location"].endswith("/?smart=1")
    assert "httponly" in callback.headers["set-cookie"].lower()

    # The state is single-use.
    replay = client.get("/api/v1/fhir/callback", params={"state": state, "code": "good-code"})
    assert replay.status_code == 400

    context = client.get("/api/v1/fhir/context")
    assert context.status_code == 200, context.text
    body = context.json()
    assert body["patient"]["name"] == "Doe, Jane" and body["patient"]["mrn"] == PATIENT_ID
    assert body["studies"][0]["study_instance_uid"] == "1.2.3"
    assert body["studies"][0]["matched_identifier"] == PATIENT_ID

    assert client.delete("/api/v1/fhir/session").status_code == 204
    assert client.get("/api/v1/fhir/context").status_code == 401


def test_smart_callback_with_bad_code_fails_cleanly(client):
    launch = client.get("/api/v1/fhir/launch", params={"iss": EHR_ISSUER})
    state = launch.headers["location"].split("state=")[1].split("&")[0]
    assert "scope=launch%2Fpatient" in launch.headers["location"]  # standalone launch
    response = client.get("/api/v1/fhir/callback", params={"state": state, "code": "bad"})
    assert response.status_code == 502
