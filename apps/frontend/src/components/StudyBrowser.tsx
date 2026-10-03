import { FolderOpen, Loader2, Search, ServerOff } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react';

import type { SmartContext } from '../lib/api/types';
import { formatDate } from '../lib/dicom/dicomJson';
import {
  loadLocalFiles,
  loadPacsStudy,
  searchStudies,
  type StudySummary,
} from '../lib/dicom/displaySets';
import { useViewerStore } from '../state/viewerStore';

const SectionTitle = ({ children }: { children: string }) => (
  <h2 className="px-3 pt-3 pb-1.5 text-[10px] font-semibold tracking-wider text-ink-faint uppercase">
    {children}
  </h2>
);

interface Props {
  /** Study to open on load (from a CDS card link: `?study=<uid>`). */
  initialStudyUid: string | null;
  /** Patient context from a SMART launch; limits the worklist to their studies. */
  smart: SmartContext | null;
}

export function StudyBrowser({ initialStudyUid, smart }: Props) {
  const displaySets = useViewerStore((state) => state.displaySets);
  const order = useViewerStore((state) => state.order);
  const shownIds = useViewerStore((state) => state.slots);
  const addDisplaySets = useViewerStore((state) => state.addDisplaySets);
  const showDisplaySet = useViewerStore((state) => state.showDisplaySet);

  const [query, setQuery] = useState('');
  const [studies, setStudies] = useState<StudySummary[]>([]);
  const [pacsState, setPacsState] = useState<'loading' | 'ready' | 'unreachable'>('loading');
  const [openingStudy, setOpeningStudy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const runSearch = useCallback(async (text: string) => {
    setPacsState('loading');
    try {
      setStudies(await searchStudies(text));
      setPacsState('ready');
    } catch {
      setStudies([]);
      setPacsState('unreachable');
    }
  }, []);

  const openStudy = useCallback(
    async (studyUid: string) => {
      setOpeningStudy(studyUid);
      setNotice(null);
      try {
        const sets = await loadPacsStudy(studyUid);
        if (sets.length === 0) setNotice('This study contains no displayable images.');
        addDisplaySets(sets);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : 'Unable to open study');
      } finally {
        setOpeningStudy(null);
      }
    },
    [addDisplaySets],
  );

  const hasSmartContext = smart !== null;
  useEffect(() => {
    // Under a SMART launch the backend has already matched the patient's studies.
    if (!hasSmartContext) void runSearch('');
  }, [runSearch, hasSmartContext]);

  useEffect(() => {
    if (initialStudyUid) void openStudy(initialStudyUid);
  }, [openStudy, initialStudyUid]);

  const openFiles = async (files: File[]) => {
    if (files.length === 0) return;
    setNotice(null);
    const { displaySets: sets, rejected } = await loadLocalFiles(files);
    addDisplaySets(sets);
    if (rejected.length > 0) {
      setNotice(`${rejected.length} file(s) were not valid DICOM and were skipped.`);
    }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    void openFiles([...event.dataTransfer.files]);
  };

  const onSearch = (event: FormEvent) => {
    event.preventDefault();
    void runSearch(query);
  };

  // Under a SMART launch, show only the launched patient's studies.
  const visibleStudies: StudySummary[] = smart
    ? smart.studies.map((study) => ({
        studyUid: study.study_instance_uid,
        patientName: smart.patient.name,
        patientId: smart.patient.mrn ?? '',
        studyDate: formatDate(study.study_date ?? undefined),
        description: study.description ?? '',
        modalities: study.modalities,
      }))
    : studies;
  const listState = smart ? (smart.pacs_available ? 'ready' : 'unreachable') : pacsState;

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-line bg-surface-1">
      <SectionTitle>Local files</SectionTitle>
      <div className="px-3">
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex w-full flex-col items-center gap-1 rounded border border-dashed px-3 py-4 text-ink-dim transition-colors ${
            dragging ? 'border-accent bg-accent/10 text-ink' : 'border-line hover:border-ink-faint'
          }`}
        >
          <FolderOpen size={18} strokeWidth={1.5} />
          <span>Open or drop DICOM files</span>
          <span className="text-[11px] text-ink-faint">Viewed in the browser; not stored</span>
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept=".dcm,.dicom,application/dicom"
          className="hidden"
          onChange={(event) => {
            void openFiles([...(event.target.files ?? [])]);
            event.target.value = '';
          }}
        />
      </div>

      {order.length > 0 && (
        <>
          <SectionTitle>Open series</SectionTitle>
          <ul className="max-h-48 overflow-y-auto px-2">
            {order.map((id) => {
              const displaySet = displaySets[id];
              const shown = shownIds.includes(id);
              return (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => showDisplaySet(id)}
                    className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-surface-3 ${
                      shown ? 'text-ink' : 'text-ink-dim'
                    }`}
                  >
                    <span className="w-7 shrink-0 rounded bg-surface-3 py-0.5 text-center font-mono text-[10px]">
                      {displaySet.modality || '—'}
                    </span>
                    <span className="min-w-0 flex-1 truncate">{displaySet.label}</span>
                    <span className="text-[11px] text-ink-faint">{displaySet.images.length}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </>
      )}

      <SectionTitle>{smart ? 'Patient studies (PACS)' : 'PACS worklist'}</SectionTitle>
      {!smart && (
        <form onSubmit={onSearch} className="relative px-3 pb-2">
          <Search size={14} className="pointer-events-none absolute top-2 left-5 text-ink-faint" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Patient name or ID"
            aria-label="Search PACS by patient name or ID"
            className="h-8 w-full rounded border border-line bg-surface-0 pr-2 pl-7 text-ink placeholder:text-ink-faint"
          />
        </form>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2">
        {listState === 'loading' && (
          <p className="flex items-center gap-2 px-2 py-3 text-ink-faint">
            <Loader2 size={14} className="animate-spin" /> Querying PACS…
          </p>
        )}
        {listState === 'unreachable' && (
          <div className="mx-1 rounded border border-line bg-surface-2 p-3 text-ink-dim">
            <p className="mb-1 flex items-center gap-2 text-ink">
              <ServerOff size={14} /> PACS not reachable
            </p>
            <p className="text-[12px] leading-relaxed">
              Start Orthanc with <code className="font-mono text-ink">docker compose up</code> in{' '}
              <code className="font-mono text-ink">docker/</code>, or open local files above.
            </p>
            {!smart && (
              <button
                type="button"
                onClick={() => void runSearch(query)}
                className="mt-2 text-accent hover:underline"
              >
                Retry
              </button>
            )}
          </div>
        )}
        {listState === 'ready' && visibleStudies.length === 0 && (
          <p className="px-2 py-3 text-ink-faint">
            {smart ? 'No PACS studies match this patient.' : 'No studies found.'}
          </p>
        )}
        <ul>
          {visibleStudies.map((study) => (
            <li key={study.studyUid}>
              <button
                type="button"
                disabled={openingStudy !== null}
                onClick={() => void openStudy(study.studyUid)}
                className="w-full rounded px-2 py-2 text-left hover:bg-surface-3 disabled:opacity-60"
              >
                <div className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-ink">
                    {study.patientName || study.patientId || 'Unknown patient'}
                  </span>
                  {openingStudy === study.studyUid && (
                    <Loader2 size={13} className="animate-spin text-accent" />
                  )}
                </div>
                <div className="truncate text-[12px] text-ink-dim">
                  {study.description || 'No description'}
                </div>
                <div className="flex gap-2 font-mono text-[11px] text-ink-faint">
                  <span>{study.studyDate || '—'}</span>
                  <span>{study.modalities.join('/')}</span>
                  <span className="truncate">{study.patientId}</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      {notice && (
        <p role="status" className="border-t border-line px-3 py-2 text-[12px] text-warn">
          {notice}
        </p>
      )}
    </aside>
  );
}
