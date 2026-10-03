import { Search } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { describeDataset, type TagRow } from '../lib/dicom/dictionary';
import { useActiveDisplaySet } from '../state/viewerStore';

/** DICOM metadata inspector: every header element of the active series' first instance. */
export function MetadataPanel() {
  const displaySet = useActiveDisplaySet();
  const [rows, setRows] = useState<TagRow[]>([]);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    let cancelled = false;
    setRows([]);
    if (displaySet) {
      void describeDataset(displaySet.header).then((described) => {
        if (!cancelled) setRows(described);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [displaySet]);

  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((row) =>
      `${row.tag} ${row.name} ${row.value}`.toLowerCase().includes(needle),
    );
  }, [rows, filter]);

  if (!displaySet) {
    return <p className="p-3 text-ink-faint">Open an image to inspect its DICOM header.</p>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative shrink-0 p-3 pb-2">
        <Search size={14} className="pointer-events-none absolute top-5 left-5 text-ink-faint" />
        <input
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter by tag, name or value"
          aria-label="Filter DICOM tags"
          className="h-8 w-full rounded border border-line bg-surface-0 pr-2 pl-7 text-ink placeholder:text-ink-faint"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-3 pb-3">
        <table className="w-full border-collapse text-left text-[12px]">
          <thead className="sticky top-0 bg-surface-1 text-[10px] tracking-wider text-ink-faint uppercase">
            <tr>
              <th className="py-1 pr-2 font-semibold">Tag</th>
              <th className="py-1 pr-2 font-semibold">VR</th>
              <th className="py-1 font-semibold">Name / value</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.key} className="border-t border-line align-top">
                <td className="py-1 pr-2 font-mono whitespace-nowrap text-ink-faint">{row.tag}</td>
                <td className="py-1 pr-2 font-mono text-ink-faint">{row.vr}</td>
                <td className="py-1" style={{ paddingLeft: row.depth * 12 }}>
                  <div className="text-ink-dim">{row.name}</div>
                  <div className="font-mono break-all text-ink">{row.value || '—'}</div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {visible.length === 0 && rows.length > 0 && (
          <p className="py-3 text-ink-faint">No tags match “{filter}”.</p>
        )}
      </div>
    </div>
  );
}
