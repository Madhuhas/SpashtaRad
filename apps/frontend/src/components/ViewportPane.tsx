import { Enums } from '@cornerstonejs/core';
import { ImageOff } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
  attachViewport,
  detachViewport,
  getEngine,
  getStackViewport,
  initViewer,
} from '../lib/cornerstone/viewer';
import { useViewerStore, viewportIdForSlot } from '../state/viewerStore';
import { HeatmapOverlay } from './HeatmapOverlay';

interface VoiRange {
  lower: number;
  upper: number;
}

/** One Cornerstone stack viewport with corner annotations and the AI overlay. */
export function ViewportPane({ slot }: { slot: number }) {
  const viewportId = viewportIdForSlot(slot);
  const [element, setElement] = useState<HTMLDivElement | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voi, setVoi] = useState<VoiRange | null>(null);
  const resizeFrame = useRef(0);

  const displaySet = useViewerStore((state) => {
    const id = state.slots[slot];
    return id ? state.displaySets[id] : undefined;
  });
  const isActive = useViewerStore((state) => state.activeSlot === slot);
  const imageIndex = useViewerStore((state) => state.imageIndex[slot]);
  const analysis = useViewerStore((state) => (displaySet ? state.analyses[displaySet.id] : undefined));
  const overlayFinding = useViewerStore((state) =>
    displaySet ? (state.overlayFinding[displaySet.id] ?? null) : null,
  );
  const overlayOpacity = useViewerStore((state) => state.overlayOpacity);
  const setActiveSlot = useViewerStore((state) => state.setActiveSlot);
  const setImageIndex = useViewerStore((state) => state.setImageIndex);

  // Enable the element as a Cornerstone viewport for the lifetime of this pane.
  useEffect(() => {
    if (!element) return;
    let cancelled = false;
    let didEnable = false;

    initViewer()
      .then(() => {
        if (cancelled) return;
        getEngine().enableElement({ viewportId, type: Enums.ViewportType.STACK, element });
        attachViewport(viewportId);
        didEnable = true;
        setEnabled(true);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : 'Viewer failed to initialise');
      });

    return () => {
      cancelled = true;
      setEnabled(false);
      if (didEnable) {
        detachViewport(viewportId);
        getEngine().disableElement(viewportId);
      }
    };
  }, [element, viewportId]);

  // Keep the rendering surface in step with the pane's size.
  useEffect(() => {
    if (!element || !enabled) return;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(resizeFrame.current);
      resizeFrame.current = requestAnimationFrame(() => getEngine().resize(true, true));
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(resizeFrame.current);
    };
  }, [element, enabled]);

  // Track the displayed image and window so the corner annotations stay current.
  useEffect(() => {
    if (!element || !enabled) return;
    const onNewImage = (event: Event) => {
      const { imageIdIndex } = (event as CustomEvent<{ imageIdIndex: number }>).detail;
      setImageIndex(slot, imageIdIndex);
    };
    const onVoi = (event: Event) => {
      const { range } = (event as CustomEvent<{ range?: VoiRange }>).detail;
      if (range) setVoi(range);
    };
    element.addEventListener(Enums.Events.STACK_NEW_IMAGE, onNewImage);
    element.addEventListener(Enums.Events.VOI_MODIFIED, onVoi);
    return () => {
      element.removeEventListener(Enums.Events.STACK_NEW_IMAGE, onNewImage);
      element.removeEventListener(Enums.Events.VOI_MODIFIED, onVoi);
    };
  }, [element, enabled, slot, setImageIndex]);

  // Load the display set's images into the viewport.
  const displaySetId = displaySet?.id;
  useEffect(() => {
    if (!enabled || !displaySet) return;
    let cancelled = false;
    setError(null);
    const viewport = getStackViewport(viewportId);
    viewport
      ?.setStack(displaySet.images.map((image) => image.imageId))
      .then(() => {
        if (cancelled) return;
        viewport.render();
        const range = viewport.getProperties().voiRange;
        if (range) setVoi(range);
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Image failed to load');
      });
    return () => {
      cancelled = true;
    };
    // Keyed on the id: the display set object is immutable once created.
  }, [enabled, viewportId, displaySetId]);

  // The heatmap belongs to the analysed image only, not the whole series.
  const finding =
    analysis?.result && overlayFinding !== null && analysis.imageIndex === imageIndex
      ? analysis.result.findings.find((item) => item.index === overlayFinding)
      : undefined;

  return (
    <div
      className={`relative min-h-0 min-w-0 overflow-hidden bg-black outline outline-1 ${
        isActive ? 'outline-accent' : 'outline-line'
      }`}
      onPointerDownCapture={() => setActiveSlot(slot)}
      // Right-drag is bound to zoom; suppress the browser menu.
      onContextMenu={(event) => event.preventDefault()}
    >
      <div ref={setElement} className="absolute inset-0" />

      {element && enabled && (
        <HeatmapOverlay
          viewportId={viewportId}
          element={element}
          heatmapUrl={finding?.heatmap_url ?? null}
          opacity={overlayOpacity}
        />
      )}

      {displaySet ? (
        <div className="pointer-events-none absolute inset-0 p-2 font-mono text-[11px] leading-4 text-ink-dim">
          <div className="absolute top-2 left-2">
            <div className="text-ink">{displaySet.patientName || 'Unknown patient'}</div>
            <div>{displaySet.patientId}</div>
          </div>
          <div className="absolute top-2 right-2 text-right">
            <div>{displaySet.studyDate}</div>
            <div>{[displaySet.modality, displaySet.bodyPart].filter(Boolean).join(' · ')}</div>
          </div>
          <div className="absolute bottom-2 left-2">
            <div>
              Im {imageIndex + 1}/{displaySet.images.length}
            </div>
            {finding && <div className="text-accent">AI overlay: {finding.label}</div>}
          </div>
          {voi && (
            <div className="absolute right-2 bottom-2 text-right">
              W {Math.round(voi.upper - voi.lower)} / L {Math.round((voi.upper + voi.lower) / 2)}
            </div>
          )}
        </div>
      ) : (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 text-ink-faint">
          <ImageOff size={28} strokeWidth={1.25} />
          <span>Select a series to display here</span>
        </div>
      )}

      {error && (
        <div className="absolute inset-x-2 bottom-8 rounded border border-critical/40 bg-surface-1/90 px-2 py-1 text-critical">
          {error}
        </div>
      )}
    </div>
  );
}
