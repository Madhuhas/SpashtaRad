/**
 * Tag dictionary lookups and local-file header parsing, backed by dcmjs.
 * dcmjs is large, so it is loaded on first use rather than with the app shell.
 */
import { formatElement, punctuateTag, TAG, type DicomJson } from './dicomJson';

let dcmjsModule: Promise<typeof import('dcmjs')> | null = null;
const loadDcmjs = () => (dcmjsModule ??= import('dcmjs'));

export interface TagRow {
  /** Unique within the listing (nested items are path-qualified). */
  key: string;
  tag: string;
  name: string;
  vr: string;
  value: string;
  /** Nesting level inside sequences, for indentation. */
  depth: number;
}

const MAX_SEQUENCE_DEPTH = 4;

/** Flatten a dataset into display rows, descending into sequences. */
export async function describeDataset(dataset: DicomJson): Promise<TagRow[]> {
  const { dictionary } = (await loadDcmjs()).data.DicomMetaDictionary;
  const rows: TagRow[] = [];

  const walk = (current: DicomJson, depth: number, path: string) => {
    for (const tag of Object.keys(current).sort()) {
      const element = current[tag];
      const punctuated = punctuateTag(tag);
      const entry = dictionary[punctuated];
      const isPrivate = parseInt(tag.slice(0, 4), 16) % 2 === 1;
      rows.push({
        key: `${path}${tag}`,
        tag: punctuated,
        name: entry?.name ?? (isPrivate ? 'Private tag' : 'Unknown'),
        vr: element.vr ?? entry?.vr ?? '',
        value: formatElement(element),
        depth,
      });
      if (element.vr === 'SQ' && depth < MAX_SEQUENCE_DEPTH) {
        (element.Value ?? []).forEach((item, index) =>
          walk(item as DicomJson, depth + 1, `${path}${tag}.${index}.`),
        );
      }
    }
  };

  walk(dataset, 0, '');
  return rows;
}

/**
 * Read the header (everything before pixel data) of a local DICOM Part 10 file.
 * Rejects if the file is not DICOM.
 */
export async function parseFileHeader(file: Blob): Promise<DicomJson> {
  const dcmjs = await loadDcmjs();
  const parsed = dcmjs.data.DicomMessage.readFile(await file.arrayBuffer(), {
    untilTag: TAG.PixelData,
    includeUntilTagValue: false,
  });
  return { ...parsed.meta, ...parsed.dict } as DicomJson;
}
