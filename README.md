# SpashtaRad AI

A radiology workstation with AI decision support: a web DICOM viewer, a DICOMweb
gateway in front of a PACS, anatomy-routed image analysis with Grad-CAM
explanations, and EHR integration through SMART on FHIR and CDS Hooks.

> **Research and decision-support software. Not a medical device.** It has no
> regulatory clearance and must not be used for diagnosis or patient care.

## Layout

| Path | What it is |
| --- | --- |
| `apps/ai_engine` | `spashta_ai` Python package: anatomy routing, model pipelines, Grad-CAM, overlays, DICOM Secondary Capture export |
| `apps/backend` | FastAPI service: DICOMweb proxy, AI orchestration, de-identification, audit log, SMART on FHIR, CDS Hooks |
| `apps/frontend` | React + Cornerstone3D viewer (Vite, TypeScript, Tailwind, Zustand) |
| `docker/` | Compose file for a local Orthanc PACS and optional PostgreSQL |

```
browser ── Vite dev server ──► backend ──► Orthanc (QIDO / WADO / STOW)
 viewer     (same-origin         │
             proxy)              ├──► spashta_ai engine (in-process)
                                 ├──► SQLite / PostgreSQL (results, audit chain)
                                 └──► EHR FHIR server (SMART launch, CDS Hooks)
```

### AI pipelines

Each image is routed by its DICOM tags (body part, modality) to one pipeline:

| Pipeline | Model | Used for |
| --- | --- | --- |
| `chest-xrv` | TorchXRayVision DenseNet121 (`densenet121-res224-all`) | Chest radiographs, 18 pathologies, Grad-CAM heatmaps |
| `msk-mura` | DenseNet169 from a MURA-style checkpoint **you supply** | Musculoskeletal radiographs |
| `biomedclip-zero-shot` | BiomedCLIP | Fallback for everything else, and for MSK when no checkpoint is configured |

Images are de-identified before they reach a model. Results, heatmaps and an
audit event are stored for every analysis; the audit log is a hash chain that
`GET /api/v1/audit/verify` re-checks.

## Prerequisites

- Python 3.11+
- Node.js 22+
- Docker (only for the local PACS / PostgreSQL)

## Setup

Commands are PowerShell, run from the repository root.

```powershell
# Python: one virtual environment for both packages
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # CPU-only build
pip install -r apps\ai_engine\requirements.txt
pip install -r apps\backend\requirements-dev.txt

# Frontend
cd apps\frontend; npm install; cd ..\..

# Configuration (optional: every value has a development default)
Copy-Item .env.example apps\backend\.env
```

Model weights are downloaded on first use (TorchXRayVision from its release
host, BiomedCLIP from the Hugging Face Hub), so the first analysis needs network
access and takes noticeably longer.

## Run

```powershell
# 1. PACS (optional: the viewer can analyse an uploaded file without one)
docker compose -f docker/docker-compose.yml up -d

# 2. Backend, from apps\backend so that ./data and .env resolve there
cd apps\backend
uvicorn app.main:app --reload --port 8000

# 3. Viewer, in a second terminal
cd apps\frontend
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api`, `/dicomweb`,
`/cds-services` and `/fhir` to the backend, so the browser only ever talks to
one origin. Set `SPASHTA_BACKEND_URL` if the backend is not on
`http://127.0.0.1:8000`.

- API docs: http://localhost:8000/docs
- Orthanc: http://localhost:8042 (user `spashtarad`, password `spashtarad`)
- With PostgreSQL: add `--profile postgres` to the compose command,
  `pip install asyncpg`, and set `SPASHTA_DATABASE_URL` as shown in `.env.example`.

## API surface

| Route | Purpose |
| --- | --- |
| `GET /healthz`, `GET /readyz` | Liveness; readiness of database, PACS and AI pipelines |
| `GET,POST /dicomweb/{path}` | Authenticated, audited proxy to the PACS (QIDO-RS, WADO-RS, STOW-RS) |
| `POST /api/v1/dicom/upload` | Store a DICOM file in the PACS and report its anatomy routing |
| `POST /api/v1/dicom/deidentify` | Return a header-de-identified copy of a DICOM file |
| `GET /api/v1/ai/status` | Which pipelines are installed and loaded |
| `POST /api/v1/ai/analyze` | Analyse an instance held in the PACS |
| `POST /api/v1/ai/analyze-upload` | Analyse an uploaded file without storing it |
| `GET /api/v1/ai/results[/{id}]` | Stored results for a study / one result |
| `GET /api/v1/ai/results/{id}/heatmaps/{n}` | Grad-CAM overlay PNG |
| `POST /api/v1/ai/results/{id}/export` | Write a finding back to the PACS as a Secondary Capture |
| `GET /api/v1/audit/events`, `/verify` | Audit log and hash-chain verification |
| `GET /api/v1/fhir/launch`, `/callback`, `/context`; `DELETE /session` | SMART on FHIR EHR launch |
| `GET /cds-services`, `POST /cds-services/{id}` | CDS Hooks discovery and `patient-view` / `order-select` services |

## Tests and checks

```powershell
cd apps\ai_engine; pytest; ruff check .              # 22 tests; `-m "not models"` skips the ones that load weights
cd apps\backend;   pytest; ruff check .              # 42 tests
cd apps\frontend;  npm test; npm run build           # vitest, then tsc + production build
```

## Configuration

All backend settings are environment variables prefixed `SPASHTA_`; see
[.env.example](.env.example) for the full list and
[apps/backend/app/core/config.py](apps/backend/app/core/config.py) for the source of truth.

With `SPASHTA_ENVIRONMENT=production` the backend refuses to start if
authentication is still `dev`, the de-identification salt is the default, or
CORS allows `*`.

## Known limitations

Read these before relying on any output.

- **MSK pipeline has no weights.** No freely licensed MURA checkpoint is bundled
  or downloaded. Until `SPASHTA_AI_MSK_WEIGHTS_PATH` points at one,
  musculoskeletal studies fall back to zero-shot, which is far weaker.
- **Zero-shot scores are not calibrated probabilities.** They rank text
  hypotheses against each other; results mark them `calibrated: false`.
- **Terminology codes are an unverified seed map.** The ICD-10 / SNOMED CT
  mappings in `apps/backend/app/data/terminology.json` have not been reviewed by
  a clinical coder, and several labels have no mapping.
- **CDS Hooks requests are not authenticated.** The JWT that an EHR sends with
  each hook call is not verified yet; do not expose `/cds-services` publicly.
- **De-identification covers metadata only.** Text burned into the pixel data
  is not detected or removed.
- **PACS and EHR paths are tested against fakes.** The DICOMweb client and the
  SMART launch are covered by tests using an in-memory PACS and a fake EHR, not
  yet against a running Orthanc or a real EHR sandbox. `docker/docker-compose.yml`
  has not been exercised either.
- **Dev authentication trusts every request.** `SPASHTA_AUTH_MODE=dev` is for
  local work only; use `oidc` anywhere else.
- **Schema is created with `create_all`.** There are no migrations yet.
- **Models are research models** trained on public datasets; they will produce
  confident findings on images outside their training distribution.
