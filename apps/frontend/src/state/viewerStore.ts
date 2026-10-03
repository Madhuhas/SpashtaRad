import { create } from 'zustand';

import { api } from '../lib/api/client';
import type { AnalysisResult } from '../lib/api/types';
import { setPrimaryTool as bindPrimaryTool, type PrimaryTool } from '../lib/cornerstone/viewer';
import type { DisplaySet } from '../lib/dicom/displaySets';

export type Layout = '1x1' | '1x2' | '2x2';

export const LAYOUTS: Record<Layout, { slots: number; columns: number; rows: number }> = {
  '1x1': { slots: 1, columns: 1, rows: 1 },
  '1x2': { slots: 2, columns: 2, rows: 1 },
  '2x2': { slots: 4, columns: 2, rows: 2 },
};
const MAX_SLOTS = 4;

export const viewportIdForSlot = (slot: number) => `viewport-${slot}`;

export interface AnalysisState {
  status: 'running' | 'done' | 'error';
  /** Index within the display set of the image that was analysed. */
  imageIndex: number;
  result?: AnalysisResult;
  error?: string;
}

interface ViewerState {
  displaySets: Record<string, DisplaySet>;
  /** Display set ids in the order they were opened. */
  order: string[];
  layout: Layout;
  /** Display set shown in each viewport slot. */
  slots: Array<string | null>;
  activeSlot: number;
  /** Currently displayed image index, per slot. */
  imageIndex: number[];
  primaryTool: PrimaryTool;
  /** Latest analysis per display set id. */
  analyses: Record<string, AnalysisState>;
  /** Finding whose heatmap is shown, per display set id. */
  overlayFinding: Record<string, number | null>;
  overlayOpacity: number;

  addDisplaySets: (sets: DisplaySet[]) => void;
  showDisplaySet: (id: string, slot?: number) => void;
  setLayout: (layout: Layout) => void;
  setActiveSlot: (slot: number) => void;
  setImageIndex: (slot: number, index: number) => void;
  setPrimaryTool: (tool: PrimaryTool) => void;
  analyzeActive: () => Promise<void>;
  toggleOverlay: (displaySetId: string, findingIndex: number) => void;
  setOverlayOpacity: (opacity: number) => void;
}

export const useViewerStore = create<ViewerState>((set, get) => ({
  displaySets: {},
  order: [],
  layout: '1x1',
  slots: Array<string | null>(MAX_SLOTS).fill(null),
  activeSlot: 0,
  imageIndex: Array<number>(MAX_SLOTS).fill(0),
  primaryTool: 'windowLevel',
  analyses: {},
  overlayFinding: {},
  overlayOpacity: 0.8,

  addDisplaySets: (sets) => {
    if (sets.length === 0) return;
    set((state) => {
      const displaySets = { ...state.displaySets };
      const order = [...state.order];
      for (const displaySet of sets) {
        if (!displaySets[displaySet.id]) order.push(displaySet.id);
        displaySets[displaySet.id] = displaySet;
      }
      return { displaySets, order };
    });
    get().showDisplaySet(sets[0].id);
  },

  showDisplaySet: (id, slot) =>
    set((state) => {
      const target = slot ?? state.activeSlot;
      const slots = [...state.slots];
      const imageIndex = [...state.imageIndex];
      slots[target] = id;
      imageIndex[target] = 0;
      return { slots, imageIndex, activeSlot: target };
    }),

  setLayout: (layout) =>
    set((state) => ({
      layout,
      // Keep the active viewport on screen when the grid shrinks.
      activeSlot: Math.min(state.activeSlot, LAYOUTS[layout].slots - 1),
    })),

  setActiveSlot: (activeSlot) => set({ activeSlot }),

  setImageIndex: (slot, index) =>
    set((state) => {
      if (state.imageIndex[slot] === index) return state;
      const imageIndex = [...state.imageIndex];
      imageIndex[slot] = index;
      return { imageIndex };
    }),

  setPrimaryTool: (tool) => {
    const previous = get().primaryTool;
    if (tool === previous) return;
    bindPrimaryTool(tool, previous);
    set({ primaryTool: tool });
  },

  analyzeActive: async () => {
    const { slots, activeSlot, displaySets, imageIndex } = get();
    const displaySetId = slots[activeSlot];
    const displaySet = displaySetId ? displaySets[displaySetId] : undefined;
    if (!displaySetId || !displaySet) return;

    const index = imageIndex[activeSlot];
    const image = displaySet.images[index];
    const update = (analysis: AnalysisState) =>
      set((state) => ({ analyses: { ...state.analyses, [displaySetId]: analysis } }));

    update({ status: 'running', imageIndex: index });
    try {
      let result: AnalysisResult;
      if (displaySet.source === 'pacs' && displaySet.studyUid && displaySet.seriesUid && image.sopInstanceUid) {
        result = await api.analyzeInstance({
          study_instance_uid: displaySet.studyUid,
          series_instance_uid: displaySet.seriesUid,
          sop_instance_uid: image.sopInstanceUid,
        });
      } else if (image.file) {
        result = await api.analyzeFile(image.file);
      } else {
        throw new Error('This image cannot be sent for analysis');
      }
      update({ status: 'done', imageIndex: index, result });
      // Show the strongest explained finding straight away.
      const explained = result.findings.find((finding) => finding.heatmap_url);
      set((state) => ({
        overlayFinding: { ...state.overlayFinding, [displaySetId]: explained?.index ?? null },
      }));
    } catch (error) {
      update({
        status: 'error',
        imageIndex: index,
        error: error instanceof Error ? error.message : 'Analysis failed',
      });
    }
  },

  toggleOverlay: (displaySetId, findingIndex) =>
    set((state) => ({
      overlayFinding: {
        ...state.overlayFinding,
        [displaySetId]: state.overlayFinding[displaySetId] === findingIndex ? null : findingIndex,
      },
    })),

  setOverlayOpacity: (overlayOpacity) => set({ overlayOpacity }),
}));

/** The display set in the active viewport, if any. */
export function useActiveDisplaySet(): DisplaySet | undefined {
  return useViewerStore((state) => {
    const id = state.slots[state.activeSlot];
    return id ? state.displaySets[id] : undefined;
  });
}
