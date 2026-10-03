/** Shapes returned by the SpashtaRad backend (`/api/v1`). */

export type Severity = 'info' | 'warning' | 'critical';

export interface CodedConcept {
  code: string;
  display: string;
}

export interface Finding {
  index: number;
  label: string;
  probability: number;
  positive: boolean;
  /** False for zero-shot similarity scores, which are not probabilities. */
  calibrated: boolean;
  heatmap_url: string | null;
  codes: { icd10: CodedConcept | null; snomed: CodedConcept | null };
}

export interface Routing {
  region: 'CHEST' | 'MSK' | 'GENERAL';
  body_part: string | null;
  modality: string | null;
  source: 'dicom_tags' | 'description_text' | 'classifier' | 'default';
  confidence: number;
  notes: string[];
}

export interface DraftReport {
  findings_text: string[];
  impression: string[];
  codes: Array<CodedConcept & { system: 'icd10' | 'snomed'; finding: string }>;
  severity: Severity;
  disclaimer: string;
}

export interface AnalysisResult {
  id: string;
  created_at: string;
  source: 'pacs' | 'upload';
  study_instance_uid: string | null;
  series_instance_uid: string | null;
  sop_instance_uid: string | null;
  severity: Severity;
  routing: Routing;
  pipeline: string;
  model: { name: string; version: string };
  findings: Finding[];
  notes: string[];
  report: DraftReport;
  deidentification: { warnings: string[]; elements_removed: number; uids_remapped: number };
}

export interface EngineStatus {
  device: string;
  pipelines: Record<string, { available: boolean; loaded: boolean }>;
}

export interface SmartContext {
  issuer: string;
  patient: {
    fhir_id: string;
    name: string;
    birth_date: string | null;
    gender: string | null;
    mrn: string | null;
  };
  encounters: Array<{ id: string; status: string | null; type: string | null; start: string | null }>;
  studies: Array<{
    study_instance_uid: string;
    study_date: string | null;
    description: string | null;
    modalities: string[];
  }>;
  pacs_available: boolean;
}

export interface InstanceUids {
  study_instance_uid: string;
  series_instance_uid: string;
  sop_instance_uid: string;
}
