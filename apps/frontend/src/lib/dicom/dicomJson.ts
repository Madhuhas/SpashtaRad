/**
 * Helpers for the DICOM JSON Model (PS3.18 Annex F): the format returned by
 * QIDO-RS / WADO-RS metadata, and the one dcmjs produces for local files.
 */

export interface DicomElement {
  vr?: string;
  Value?: unknown[];
  BulkDataURI?: string;
  InlineBinary?: string;
}

/** Keys are 8 hex digits, e.g. "00100010". */
export type DicomJson = Record<string, DicomElement>;

export const TAG = {
  SOPInstanceUID: '00080018',
  StudyDate: '00080020',
  AccessionNumber: '00080050',
  Modality: '00080060',
  ModalitiesInStudy: '00080061',
  StudyDescription: '00081030',
  SeriesDescription: '0008103E',
  PatientName: '00100010',
  PatientID: '00100020',
  BodyPartExamined: '00180015',
  StudyInstanceUID: '0020000D',
  SeriesInstanceUID: '0020000E',
  SeriesNumber: '00200011',
  InstanceNumber: '00200013',
  NumberOfFrames: '00280008',
  Rows: '00280010',
  PixelData: '7FE00010',
} as const;

function scalarToString(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'object') {
    // Person names are objects: { Alphabetic: "Family^Given" }.
    const name = (value as { Alphabetic?: unknown }).Alphabetic;
    return typeof name === 'string' ? name : '';
  }
  return String(value);
}

export function getStrings(dataset: DicomJson, tag: string): string[] {
  return (dataset[tag]?.Value ?? []).map(scalarToString).filter(Boolean);
}

export function getString(dataset: DicomJson, tag: string): string | undefined {
  return getStrings(dataset, tag)[0];
}

export function getNumber(dataset: DicomJson, tag: string): number | undefined {
  const value = Number(getString(dataset, tag));
  return Number.isFinite(value) ? value : undefined;
}

/** "DOE^JANE^M" -> "DOE, JANE M" */
export function formatPersonName(raw: string | undefined): string {
  if (!raw) return '';
  const [family = '', ...rest] = raw.split('^');
  const given = rest.filter(Boolean).join(' ');
  return given ? `${family}, ${given}` : family;
}

/** "20240315" -> "2024-03-15"; anything else is returned unchanged. */
export function formatDate(raw: string | undefined): string {
  if (!raw) return '';
  return /^\d{8}$/.test(raw) ? `${raw.slice(0, 4)}-${raw.slice(4, 6)}-${raw.slice(6)}` : raw;
}

/** "00100010" -> "(0010,0010)" */
export function punctuateTag(tag: string): string {
  return `(${tag.slice(0, 4)},${tag.slice(4, 8)})`.toUpperCase();
}

const MAX_VALUE_LENGTH = 160;

/** Human-readable rendering of one element for the metadata inspector. */
export function formatElement(element: DicomElement): string {
  if (element.vr === 'SQ') {
    const count = element.Value?.length ?? 0;
    return `Sequence (${count} item${count === 1 ? '' : 's'})`;
  }
  if (element.BulkDataURI || element.InlineBinary) return '<binary data>';
  const values = element.Value ?? [];
  if (values.some((value) => value instanceof ArrayBuffer || ArrayBuffer.isView(value))) {
    return '<binary data>';
  }
  const text = values.map(scalarToString).join(' \\ ');
  return text.length > MAX_VALUE_LENGTH ? `${text.slice(0, MAX_VALUE_LENGTH)}…` : text;
}
