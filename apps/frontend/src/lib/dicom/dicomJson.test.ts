import { describe, expect, it } from 'vitest';

import { formatElement, getString, getStrings, type DicomJson } from './dicomJson';

describe('formatElement', () => {
  it('joins multiple values', () => {
    expect(formatElement({ vr: 'CS', Value: ['DERIVED', 'PRIMARY'] })).toBe('DERIVED \\ PRIMARY');
  });

  it('summarises sequences and binary values', () => {
    expect(formatElement({ vr: 'SQ', Value: [{}, {}] })).toBe('Sequence (2 items)');
    expect(formatElement({ vr: 'OB', Value: [new Uint8Array(4)] })).toBe('<binary data>');
    expect(formatElement({ vr: 'OB', InlineBinary: 'AAEC' })).toBe('<binary data>');
  });

  // dcmjs returns a bare buffer or scalar (not an array) for some local-file
  // elements, e.g. FileMetaInformationVersion; this used to crash the tags tab.
  it('accepts a Value that is not an array', () => {
    const bare = (value: unknown) => ({ vr: 'OB', Value: value }) as never;
    expect(formatElement(bare(new ArrayBuffer(2)))).toBe('<binary data>');
    expect(formatElement(bare('ISO_IR 192'))).toBe('ISO_IR 192');
  });
});

describe('getStrings', () => {
  it('reads person names and tolerates a non-array Value', () => {
    const dataset = {
      '00100010': { vr: 'PN', Value: [{ Alphabetic: 'DOE^JANE' }] },
      '00080060': { vr: 'CS', Value: 'CR' },
    } as unknown as DicomJson;
    expect(getString(dataset, '00100010')).toBe('DOE^JANE');
    expect(getStrings(dataset, '00080060')).toEqual(['CR']);
    expect(getString(dataset, '00080018')).toBeUndefined();
  });
});
