import { useState } from 'react';

import { FindingsPanel } from './FindingsPanel';
import { MetadataPanel } from './MetadataPanel';
import { ReportPanel } from './ReportPanel';

const TABS = [
  { id: 'findings', label: 'AI findings' },
  { id: 'report', label: 'Draft report' },
  { id: 'metadata', label: 'DICOM tags' },
] as const;

type TabId = (typeof TABS)[number]['id'];

export function SidePanel() {
  const [tab, setTab] = useState<TabId>('findings');

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-line bg-surface-1">
      <div role="tablist" aria-label="Study details" className="flex shrink-0 border-b border-line">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={tab === item.id}
            onClick={() => setTab(item.id)}
            className={`h-10 flex-1 border-b-2 text-[12px] font-medium transition-colors ${
              tab === item.id
                ? 'border-accent text-ink'
                : 'border-transparent text-ink-faint hover:text-ink-dim'
            }`}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        {tab === 'findings' && <FindingsPanel />}
        {tab === 'report' && <ReportPanel />}
        {tab === 'metadata' && <MetadataPanel />}
      </div>
    </aside>
  );
}
