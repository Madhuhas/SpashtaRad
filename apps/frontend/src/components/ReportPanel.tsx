import { Check, Copy } from 'lucide-react';
import { useState } from 'react';

import type { AnalysisResult } from '../lib/api/types';
import { useActiveDisplaySet, useViewerStore } from '../state/viewerStore';

const SYSTEM_LABEL = { icd10: 'ICD-10-CM', snomed: 'SNOMED CT' } as const;

function reportAsText(result: AnalysisResult): string {
  const { report } = result;
  return [
    'FINDINGS (AI draft)',
    ...report.findings_text.map((line) => `- ${line}`),
    '',
    'IMPRESSION (AI draft)',
    ...report.impression.map((line, index) => `${index + 1}. ${line}`),
    '',
    ...(report.codes.length
      ? ['CODES', ...report.codes.map((c) => `${SYSTEM_LABEL[c.system]} ${c.code} ${c.display}`), '']
      : []),
    `Model: ${result.model.name} (${result.model.version})`,
    report.disclaimer,
  ].join('\n');
}

const Heading = ({ children }: { children: string }) => (
  <h3 className="mb-1.5 text-[10px] font-semibold tracking-wider text-ink-faint uppercase">
    {children}
  </h3>
);

/** Structured draft impression with coded concepts, for radiologist review. */
export function ReportPanel() {
  const displaySet = useActiveDisplaySet();
  const result = useViewerStore((state) =>
    displaySet ? state.analyses[displaySet.id]?.result : undefined,
  );
  const [copied, setCopied] = useState(false);

  if (!result) {
    return <p className="p-3 text-ink-faint">Run AI analysis to generate a draft impression.</p>;
  }
  const { report } = result;

  const copy = async () => {
    await navigator.clipboard.writeText(reportAsText(result));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="flex flex-col gap-4 p-3">
      <p className="rounded border border-warn/40 bg-warn/10 p-2 text-[12px] leading-relaxed text-warn">
        {report.disclaimer}
      </p>

      <section>
        <Heading>Impression</Heading>
        <ol className="list-decimal space-y-1 pl-4 text-ink">
          {report.impression.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ol>
      </section>

      <section>
        <Heading>Findings</Heading>
        <ul className="space-y-1 text-ink-dim">
          {report.findings_text.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </section>

      {report.codes.length > 0 && (
        <section>
          <Heading>Suggested codes</Heading>
          <table className="w-full text-left text-[12px]">
            <tbody>
              {report.codes.map((code) => (
                <tr key={`${code.system}:${code.code}:${code.finding}`} className="border-t border-line">
                  <td className="py-1 pr-2 text-ink-faint">{SYSTEM_LABEL[code.system]}</td>
                  <td className="py-1 pr-2 font-mono text-ink">{code.code}</td>
                  <td className="py-1 text-ink-dim">{code.display}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-1.5 text-[11px] text-ink-faint">
            Codes are suggestions from an unverified seed mapping; confirm before use.
          </p>
        </section>
      )}

      <button
        type="button"
        onClick={() => void copy()}
        className="flex h-8 items-center justify-center gap-2 rounded border border-line text-ink-dim hover:border-ink-faint hover:text-ink"
      >
        {copied ? <Check size={14} className="text-accent" /> : <Copy size={14} />}
        {copied ? 'Copied' : 'Copy draft'}
      </button>
    </div>
  );
}
