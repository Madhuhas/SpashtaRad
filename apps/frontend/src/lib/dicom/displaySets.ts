/**
 * Display sets: the viewer's unit of "something you can hang in a viewport".
 * One display set is one image series, whether it came from the PACS (over
 * DICOMweb) or from files opened locally.
 */
import { wadors, wadouri } from '@cornerstonejs/dicom-image-loader';

import { api } from '../api/client';
import { parseFileHeader } from './dictionary';
import {
  formatDate,
  formatPersonName,
  getNumber,
  getString,
  getStrings,
  TAG,
  type DicomJson,
} from './dicomJson';

export interface DisplayImage {
  /** Cornerstone image id (`wadors:` for PACS, `dicomfile:` for local files). */
  imageId: string;
  sopInstanceUid?: string;
  /** Present for locally opened files; used to upload the image for analysis. */
  file?: Blob;
}

export interface DisplaySet {
  id: string;
  source: 'pacs' | 'file';
  studyUid?: string;
  seriesUid?: string;
  label: string;
  modality: string;
  bodyPart: string;
  patientName: string;
  patientId: string;
  studyDate: string;
  images: DisplayImage[];
  /** Header of the first instance, for the metadata inspector. */
  header: DicomJson;
}

export interface StudySummary {
  studyUid: string;
  patientName: string;
  patientId: string;
  studyDate: string;
  description: string;
  modalities: string[];
}

const STUDY_FIELDS = [TAG.StudyDescription, TAG.ModalitiesInStudy, TAG.PatientName, TAG.PatientID];

/** QIDO-RS study search. A purely numeric query matches PatientID, otherwise PatientName. */
export async function searchStudies(query: string): Promise<StudySummary[]> {
  const params: Record<string, string> = { limit: '50', includefield: STUDY_FIELDS.join(',') };
  const trimmed = query.trim();
  if (trimmed) {
    params[/^[\d-]+$/.test(trimmed) ? 'PatientID' : 'PatientName'] = `*${trimmed}*`;
  }
  const studies = await api.dicomweb<DicomJson[]>('studies', params);
  return studies
    .map((study) => ({
      studyUid: getString(study, TAG.StudyInstanceUID) ?? '',
      patientName: formatPersonName(getString(study, TAG.PatientName)),
      patientId: getString(study, TAG.PatientID) ?? '',
      studyDate: formatDate(getString(study, TAG.StudyDate)),
      description: getString(study, TAG.StudyDescription) ?? '',
      modalities: getStrings(study, TAG.ModalitiesInStudy),
    }))
    .filter((study) => study.studyUid)
    .sort((a, b) => b.studyDate.localeCompare(a.studyDate));
}

function describe(header: DicomJson, fallbackLabel: string) {
  return {
    label:
      getString(header, TAG.SeriesDescription) ??
      getString(header, TAG.StudyDescription) ??
      fallbackLabel,
    modality: getString(header, TAG.Modality) ?? '',
    bodyPart: getString(header, TAG.BodyPartExamined) ?? '',
    patientName: formatPersonName(getString(header, TAG.PatientName)),
    patientId: getString(header, TAG.PatientID) ?? '',
    studyDate: formatDate(getString(header, TAG.StudyDate)),
  };
}

const byInstanceNumber = (a: DicomJson, b: DicomJson) =>
  (getNumber(a, TAG.InstanceNumber) ?? 0) - (getNumber(b, TAG.InstanceNumber) ?? 0);

/**
 * Load every image series of a PACS study.
 *
 * For each instance the WADO-RS metadata is registered with Cornerstone's
 * `wadors` loader, which then fetches pixel data frame by frame on demand.
 */
export async function loadPacsStudy(studyUid: string): Promise<DisplaySet[]> {
  const root = `${window.location.origin}/dicomweb`;
  const series = await api.dicomweb<DicomJson[]>(`studies/${studyUid}/series`);
  series.sort((a, b) => (getNumber(a, TAG.SeriesNumber) ?? 0) - (getNumber(b, TAG.SeriesNumber) ?? 0));

  const displaySets: DisplaySet[] = [];
  for (const item of series) {
    const seriesUid = getString(item, TAG.SeriesInstanceUID);
    if (!seriesUid) continue;

    const instances = await api.dicomweb<DicomJson[]>(
      `studies/${studyUid}/series/${seriesUid}/metadata`,
    );
    // Skip non-image objects (structured reports, presentation states, ...).
    const imageInstances = instances.filter((instance) => instance[TAG.Rows]).sort(byInstanceNumber);
    if (imageInstances.length === 0) continue;

    const images: DisplayImage[] = [];
    for (const instance of imageInstances) {
      const sopInstanceUid = getString(instance, TAG.SOPInstanceUID);
      if (!sopInstanceUid) continue;
      const frames = getNumber(instance, TAG.NumberOfFrames) ?? 1;
      for (let frame = 1; frame <= frames; frame += 1) {
        const imageId = `wadors:${root}/studies/${studyUid}/series/${seriesUid}/instances/${sopInstanceUid}/frames/${frame}`;
        wadors.metaDataManager.add(imageId, instance as never);
        images.push({ imageId, sopInstanceUid });
      }
    }

    const header = imageInstances[0];
    displaySets.push({
      id: `pacs:${seriesUid}`,
      source: 'pacs',
      studyUid,
      seriesUid,
      images,
      header,
      ...describe(header, `Series ${getString(item, TAG.SeriesNumber) ?? ''}`.trim()),
    });
  }
  return displaySets;
}

export interface LocalLoadResult {
  displaySets: DisplaySet[];
  /** Names of files that were not readable DICOM. */
  rejected: string[];
}

/** Group locally opened DICOM files into display sets by series. */
export async function loadLocalFiles(files: File[]): Promise<LocalLoadResult> {
  const groups = new Map<string, Array<{ file: File; header: DicomJson }>>();
  const rejected: string[] = [];

  for (const file of files) {
    try {
      const header = await parseFileHeader(file);
      const seriesUid = getString(header, TAG.SeriesInstanceUID) ?? `file:${file.name}`;
      const group = groups.get(seriesUid) ?? [];
      group.push({ file, header });
      groups.set(seriesUid, group);
    } catch {
      rejected.push(file.name);
    }
  }

  const displaySets = [...groups.entries()].map(([seriesUid, members]): DisplaySet => {
    members.sort((a, b) => byInstanceNumber(a.header, b.header));
    const header = members[0].header;
    return {
      // A timestamp keeps re-opened files distinct from earlier loads.
      id: `file:${seriesUid}:${Date.now()}`,
      source: 'file',
      studyUid: getString(header, TAG.StudyInstanceUID),
      seriesUid: getString(header, TAG.SeriesInstanceUID),
      images: members.map(({ file, header: instanceHeader }) => ({
        imageId: wadouri.fileManager.add(file),
        sopInstanceUid: getString(instanceHeader, TAG.SOPInstanceUID),
        file,
      })),
      header,
      ...describe(header, members[0].file.name),
    };
  });
  return { displaySets, rejected };
}
