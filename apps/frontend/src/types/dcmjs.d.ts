/** Minimal typings for the parts of dcmjs this app uses (the package ships none). */
declare module 'dcmjs' {
  interface DictionaryEntry {
    tag: string;
    vr: string;
    name: string;
    vm: string;
  }

  interface DicomDict {
    meta: Record<string, { vr?: string; Value?: unknown[] }>;
    dict: Record<string, { vr?: string; Value?: unknown[] }>;
  }

  export const data: {
    DicomMessage: {
      readFile(
        buffer: ArrayBuffer,
        options?: { untilTag?: string; includeUntilTagValue?: boolean; ignoreErrors?: boolean },
      ): DicomDict;
    };
    DicomMetaDictionary: {
      /** Keyed by punctuated tag, e.g. "(0010,0010)". */
      dictionary: Record<string, DictionaryEntry | undefined>;
    };
  };
}
