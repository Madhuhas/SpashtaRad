import { Activity, LogOut, ScanLine, UserRound } from 'lucide-react';
import { useEffect, useState } from 'react';

import { api } from '../lib/api/client';
import type { EngineStatus, SmartContext } from '../lib/api/types';

const SMART_SANDBOX_ISSUER = 'https://launch.smarthealthit.org/v/r4/fhir';

interface Props {
  smart: SmartContext | null;
  onSmartEnded: () => void;
}

function EngineIndicator() {
  const [status, setStatus] = useState<EngineStatus | 'offline' | null>(null);

  useEffect(() => {
    api
      .engineStatus()
      .then(setStatus)
      .catch(() => setStatus('offline'));
  }, []);

  if (status === null) return null;
  if (status === 'offline') {
    return (
      <span className="flex items-center gap-1.5 text-critical" title="The backend API is not reachable">
        <Activity size={14} /> API offline
      </span>
    );
  }
  const pipelines = Object.entries(status.pipelines);
  const available = pipelines.filter(([, pipeline]) => pipeline.available);
  return (
    <span
      className={`flex items-center gap-1.5 ${available.length ? 'text-ink-dim' : 'text-warn'}`}
      title={pipelines
        .map(([name, pipeline]) => `${name}: ${pipeline.available ? 'available' : 'not installed'}`)
        .join('\n')}
    >
      <Activity size={14} />
      {available.length}/{pipelines.length} AI pipelines · {status.device}
    </span>
  );
}

export function TopBar({ smart, onSmartEnded }: Props) {
  const endSession = async () => {
    await api.endSmartSession().catch(() => undefined);
    onSmartEnded();
  };

  return (
    <header className="flex h-11 shrink-0 items-center gap-4 border-b border-line bg-surface-1 px-3">
      <div className="flex items-center gap-2">
        <ScanLine size={18} className="text-accent" />
        <span className="font-semibold tracking-tight">SpashtaRad AI</span>
      </div>
      <span
        className="rounded border border-warn/50 px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-warn uppercase"
        title="This software is not a cleared medical device and must not be used for diagnosis."
      >
        Research use only
      </span>

      <div className="ml-auto flex items-center gap-4 text-[12px]">
        {smart ? (
          <div className="flex items-center gap-2 rounded bg-surface-2 px-2.5 py-1">
            <UserRound size={14} className="text-accent" />
            <span className="text-ink">{smart.patient.name}</span>
            <span className="font-mono text-ink-faint">
              {[smart.patient.mrn && `MRN ${smart.patient.mrn}`, smart.patient.birth_date, smart.patient.gender]
                .filter(Boolean)
                .join(' · ')}
            </span>
            <button
              type="button"
              onClick={() => void endSession()}
              title="End EHR session"
              aria-label="End EHR session"
              className="ml-1 text-ink-faint hover:text-ink"
            >
              <LogOut size={14} />
            </button>
          </div>
        ) : (
          <a
            href={`/fhir/launch?iss=${encodeURIComponent(SMART_SANDBOX_ISSUER)}`}
            className="text-ink-dim hover:text-accent"
            title="Standalone SMART on FHIR launch against the public SMART Health IT sandbox"
          >
            Connect EHR (SMART sandbox)
          </a>
        )}
        <EngineIndicator />
      </div>
    </header>
  );
}
