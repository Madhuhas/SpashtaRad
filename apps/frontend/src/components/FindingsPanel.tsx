import { AlertTriangle, Eye, EyeOff, Loader2, Send, Sparkles } from 'lucide-react';
import { useState } from 'react';

import { api } from '../lib/api/client';
import type { AnalysisResult, Finding, Severity } from '../lib/api/types';
import { useActiveDisplaySet, useViewerStore } from '../state/viewerStore';

const SEVERITY_STYLE: Record<Severity, string> = {
  info: 'border-line text-ink-dim',
  warning: 'border-warn/50 text-warn',
  critical: 'border-critical/60 text-critical',
};

const SEVERITY_LABEL: Record<Severity, string> = {
  info: 'No notable finding',
  warning: 'Findings to review',
  critical: 'Urgent review suggested',
};

const ROUTING_SOURCE: Record<AnalysisResult['routing']['source'], string> = {
  dicom_tags: 'DICOM header',
  description_text: 'study description',
  classifier: 'image classifier',
  default: 'default',
};

function FindingRow({
  finding,
  overlayShown,
  onToggleOverlay,
}: {
  finding: Finding;
  overlayShown: boolean;
  onToggleOverlay: () => void;
}) {
  const percent = Math.round(finding.probability * 100);
  const codes = [finding.codes.icd10 && `ICD-10 ${finding.codes.icd10.code}`, finding.codes.snomed && `SCT ${finding.codes.snomed.code}`]
    .filter(Boolean)
    .join(' · ');

  return (
    <li className={`rounded px-2 py-1.5 ${finding.positive ? 'bg-surface-2' : ''}`}>
      <div className="flex items-center gap-2">
        <span className={`min-w-0 flex-1 truncate ${finding.positive ? 'text-ink' : 'text-ink-dim'}`}>
          {finding.label}
        </span>
        <span className="w-9 text-right font-mono text-[12px] text-ink-dim tabular-nums">
          {percent}%
        </span>
        {finding.heatmap_url ? (
          <button
            type="button"
            onClick={onToggleOverlay}
            aria-pressed={overlayShown}
            title={overlayShown ? 'Hide heatmap' : 'Show heatmap'}
            aria-label={`${overlayShown ? 'Hide' : 'Show'} heatmap for ${finding.label}`}
            className={`flex h-6 w-6 items-center justify-center rounded ${
              overlayShown ? 'bg-accent/15 text-accent' : 'text-ink-faint hover:text-ink'
            }`}
          >
            {overlayShown ? <Eye size={14} /> : <EyeOff size={14} />}
          </button>
        ) : (
          <span className="w-6" />
        )}
      </div>
      <div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-3">
        <div
          className={`h-full rounded-full ${finding.positive ? 'bg-accent' : 'bg-ink-faint'}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      {finding.positive && codes && (
        <div className="mt-1 font-mono text-[10.5px] text-ink-faint">{codes}</div>
      )}
    </li>
  );
}

export function FindingsPanel() {
  const displaySet = useActiveDisplaySet();
  const analysis = useViewerStore((state) => (displaySet ? state.analyses[displaySet.id] : undefined));
  const overlayFinding = useViewerStore((state) =>
    displaySet ? (state.overlayFinding[displaySet.id] ?? null) : null,
  );
  const opacity = useViewerStore((state) => state.overlayOpacity);
  const analyzeActive = useViewerStore((state) => state.analyzeActive);
  const toggleOverlay = useViewerStore((state) => state.toggleOverlay);
  const setOpacity = useViewerStore((state) => state.setOverlayOpacity);
  const [exportState, setExportState] = useState<{ status: 'idle' | 'busy' | 'done' | 'error'; message?: string }>({ status: 'idle' });

  const running = analysis?.status === 'running';
  const result = analysis?.result;

  const exportToPacs = async () => {
    if (!result || overlayFinding === null) return;
    setExportState({ status: 'busy' });
    try {
      await api.exportFinding(result.id, overlayFinding);
      setExportState({ status: 'done', message: 'Stored in PACS as a new series of this study.' });
    } catch (error) {
      setExportState({
        status: 'error',
        message: error instanceof Error ? error.message : 'Export failed',
      });
    }
  };

  const notes = result
    ? [...result.routing.notes, ...result.notes, ...result.deidentification.warnings]
    : [];
  const anyUncalibrated = result?.findings.some((finding) => !finding.calibrated) ?? false;

  return (
    <div className="flex flex-col gap-3 p-3">
      <button
        type="button"
        disabled={!displaySet || running}
        onClick={() => {
          setExportState({ status: 'idle' });
          void analyzeActive();
        }}
        className="flex h-9 items-center justify-center gap-2 rounded bg-accent-strong font-medium text-surface-0 transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:bg-surface-3 disabled:text-ink-faint"
      >
        {running ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
        {running ? 'Analysing…' : result ? 'Re-run analysis' : 'Analyse displayed image'}
      </button>

      {!displaySet && <p className="text-ink-faint">Open an image to run AI analysis.</p>}
      {running && (
        <p className="text-[12px] text-ink-faint">
          The first run loads model weights and can take a minute on CPU.
        </p>
      )}
      {analysis?.status === 'error' && (
        <p role="alert" className="flex gap-2 rounded border border-critical/40 bg-critical/10 p-2 text-critical">
          <AlertTriangle size={15} className="mt-0.5 shrink-0" />
          <span>{analysis.error}</span>
        </p>
      )}

      {result && (
        <>
          <div className={`rounded border px-2.5 py-2 ${SEVERITY_STYLE[result.severity]}`}>
            <div className="font-medium">{SEVERITY_LABEL[result.severity]}</div>
            <div className="mt-0.5 text-[12px] text-ink-dim">
              {result.routing.region}
              {result.routing.body_part ? ` · ${result.routing.body_part}` : ''} via{' '}
              {ROUTING_SOURCE[result.routing.source]}
            </div>
            <div className="truncate font-mono text-[11px] text-ink-faint" title={`${result.model.name} ${result.model.version}`}>
              {result.pipeline} · {result.model.name}
            </div>
          </div>

          <ul className="flex flex-col gap-0.5">
            {result.findings.map((finding) => (
              <FindingRow
                key={finding.index}
                finding={finding}
                overlayShown={overlayFinding === finding.index}
                onToggleOverlay={() => displaySet && toggleOverlay(displaySet.id, finding.index)}
              />
            ))}
          </ul>

          {anyUncalibrated && (
            <p className="text-[12px] text-ink-faint">
              Scores are zero-shot similarities, not calibrated probabilities.
            </p>
          )}

          {overlayFinding !== null && (
            <div className="flex flex-col gap-2 rounded border border-line p-2.5">
              <label className="flex items-center gap-3 text-ink-dim">
                <span className="shrink-0">Heatmap opacity</span>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={opacity}
                  onChange={(event) => setOpacity(Number(event.target.value))}
                  className="min-w-0 flex-1 accent-accent"
                />
              </label>
              {result.source === 'pacs' && (
                <button
                  type="button"
                  disabled={exportState.status === 'busy'}
                  onClick={() => void exportToPacs()}
                  className="flex h-8 items-center justify-center gap-2 rounded border border-line text-ink-dim hover:border-ink-faint hover:text-ink disabled:opacity-60"
                >
                  {exportState.status === 'busy' ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Send size={14} />
                  )}
                  Send overlay to PACS
                </button>
              )}
              {exportState.message && (
                <p
                  role="status"
                  className={`text-[12px] ${exportState.status === 'error' ? 'text-critical' : 'text-accent'}`}
                >
                  {exportState.message}
                </p>
              )}
            </div>
          )}

          {notes.length > 0 && (
            <ul className="list-disc space-y-1 pl-4 text-[12px] text-ink-faint">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
