/**
 * Cornerstone3D bootstrap: one rendering engine and one tool group for the
 * whole app. Components call `initViewer()` and await it before touching
 * any Cornerstone API.
 */
import {
  init as initCore,
  metaData,
  RenderingEngine,
  type Types,
  utilities,
} from '@cornerstonejs/core';
import { init as initImageLoader } from '@cornerstonejs/dicom-image-loader';
import {
  addTool,
  AngleTool,
  Enums as ToolEnums,
  init as initTools,
  LengthTool,
  PanTool,
  StackScrollTool,
  ToolGroupManager,
  WindowLevelTool,
  ZoomTool,
} from '@cornerstonejs/tools';

import { authHeaders } from '../api/client';

export const RENDERING_ENGINE_ID = 'spashtarad-engine';
const TOOL_GROUP_ID = 'spashtarad-tools';

/** Tools selectable for the primary (left) mouse button. */
export const PRIMARY_TOOLS = {
  windowLevel: WindowLevelTool.toolName,
  pan: PanTool.toolName,
  zoom: ZoomTool.toolName,
  length: LengthTool.toolName,
  angle: AngleTool.toolName,
} as const;

export type PrimaryTool = keyof typeof PRIMARY_TOOLS;

const { MouseBindings } = ToolEnums;

let ready: Promise<void> | null = null;
let engine: RenderingEngine | null = null;

interface LengthStats {
  length?: number;
  unit?: string;
}

/**
 * Label for a length measurement. Without pixel spacing Cornerstone's default
 * reads "420 px Unknown" (unit plus calibration type); show just "420 px".
 */
function lengthLabel(
  data: { cachedStats?: Record<string, LengthStats> },
  targetId: string,
): string[] | undefined {
  const { length, unit } = data.cachedStats?.[targetId] ?? {};
  if (length === undefined || length === null) return undefined;
  return [`${utilities.roundNumber(length)} ${(unit ?? '').replace(/\s*Unknown$/i, '')}`.trim()];
}

function setUpTools(): void {
  for (const tool of [WindowLevelTool, PanTool, ZoomTool, StackScrollTool, LengthTool, AngleTool]) {
    addTool(tool);
  }
  const group = ToolGroupManager.createToolGroup(TOOL_GROUP_ID);
  if (!group) throw new Error('Unable to create Cornerstone tool group');

  for (const toolName of [...Object.values(PRIMARY_TOOLS), StackScrollTool.toolName]) {
    group.addTool(toolName, toolName === LengthTool.toolName ? { getTextLines: lengthLabel } : undefined);
  }
  // Fixed bindings that hold regardless of the selected primary tool.
  group.setToolActive(WindowLevelTool.toolName, {
    bindings: [{ mouseButton: MouseBindings.Primary }],
  });
  group.setToolActive(PanTool.toolName, { bindings: [{ mouseButton: MouseBindings.Auxiliary }] });
  group.setToolActive(ZoomTool.toolName, { bindings: [{ mouseButton: MouseBindings.Secondary }] });
  group.setToolActive(StackScrollTool.toolName, { bindings: [{ mouseButton: MouseBindings.Wheel }] });
  // Measurement tools start passive: existing annotations stay visible and editable.
  group.setToolPassive(LengthTool.toolName);
  group.setToolPassive(AngleTool.toolName);
}

const SPACING_FIELDS = ['rowPixelSpacing', 'columnPixelSpacing'] as const;
const SPACING_PRIORITY = 100_000;
let sanitisingSpacing = false;

/**
 * Some files declare a pixel spacing of zero (or less). Taken literally the
 * image has no extent, the camera becomes NaN and nothing is drawn. Treat
 * such spacing as absent instead.
 */
function spacingSanitiser(type: string, imageId: string): unknown {
  if (type !== 'imagePlaneModule' || sanitisingSpacing) return undefined;
  sanitisingSpacing = true;
  let plane: Record<string, unknown> | undefined;
  try {
    // Re-entrant lookup: the guard above makes it fall through to the real providers.
    plane = metaData.get(type, imageId) as Record<string, unknown> | undefined;
  } finally {
    sanitisingSpacing = false;
  }
  if (!plane) return undefined;

  const invalid = SPACING_FIELDS.some((field) => {
    const value = plane[field];
    return value !== undefined && value !== null && !(Number(value) > 0);
  });
  if (!invalid) return undefined;
  // The values Cornerstone itself substitutes for absent spacing. The flag marks
  // the image as uncalibrated, so measurements are reported in pixels, not mm.
  return {
    ...plane,
    rowPixelSpacing: 1,
    columnPixelSpacing: 1,
    pixelSpacing: [1, 1],
    usingDefaultValues: true,
  };
}

/** Initialise Cornerstone once. Safe to call from every component. */
export function initViewer(): Promise<void> {
  ready ??= (async () => {
    await initCore();
    metaData.addProvider(spacingSanitiser, SPACING_PRIORITY);
    initImageLoader({
      maxWebWorkers: Math.max(1, Math.min(navigator.hardwareConcurrency ?? 2, 4)),
      // Pixel-data requests go through the backend gateway and need the same
      // credentials as every other API call.
      beforeSend: () => authHeaders(),
    });
    initTools();
    setUpTools();
    engine = new RenderingEngine(RENDERING_ENGINE_ID);
  })();
  return ready;
}

export function getEngine(): RenderingEngine {
  if (!engine) throw new Error('initViewer() has not completed');
  return engine;
}

let resizeFrame = 0;

/**
 * Re-fit every viewport to its element after a size change (layout switch,
 * window resize). Zoom and pan are stored relative to "fit", so the image is
 * refitted to the new shape while keeping what the user had set up.
 */
export function resizeViewports(): void {
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => {
    if (!engine) return;
    const viewports = engine.getViewports() as Types.IStackViewport[];
    const presentations = viewports.map((viewport) =>
      viewport.getCurrentImageId() ? viewport.getViewPresentation() : undefined,
    );
    engine.resize(true, false);
    viewports.forEach((viewport, index) => {
      const presentation = presentations[index];
      if (!presentation) return;
      viewport.setViewPresentation(presentation);
      viewport.render();
    });
  });
}

function toolGroup() {
  const group = ToolGroupManager.getToolGroup(TOOL_GROUP_ID);
  if (!group) throw new Error('initViewer() has not completed');
  return group;
}

export function attachViewport(viewportId: string): void {
  toolGroup().addViewport(viewportId, RENDERING_ENGINE_ID);
}

export function detachViewport(viewportId: string): void {
  ToolGroupManager.getToolGroup(TOOL_GROUP_ID)?.removeViewports(RENDERING_ENGINE_ID, viewportId);
}

/** Bind `tool` to the primary mouse button, releasing the previous one. */
export function setPrimaryTool(tool: PrimaryTool, previous: PrimaryTool): void {
  const group = toolGroup();
  // setToolPassive drops only the primary-button binding, so Pan and Zoom keep
  // their fixed middle/right buttons. setToolActive *adds* to a tool's existing
  // bindings, so it must not be used to release the outgoing tool.
  group.setToolPassive(PRIMARY_TOOLS[previous]);
  group.setToolActive(PRIMARY_TOOLS[tool], { bindings: [{ mouseButton: MouseBindings.Primary }] });
}

export function getStackViewport(viewportId: string): Types.IStackViewport | undefined {
  return engine?.getViewport<Types.IStackViewport>(viewportId) ?? undefined;
}

export type ViewportAction = 'reset' | 'invert' | 'rotate' | 'flip';

type DisplayDefaults = Pick<Types.StackViewportProperties, 'voiRange' | 'invert'>;
const displayDefaults = new Map<string, DisplayDefaults>();

/** Record the window and polarity a stack was first shown with, for "Reset view". */
export function rememberDisplayDefaults(viewportId: string): void {
  const { voiRange, invert } = getStackViewport(viewportId)?.getProperties() ?? {};
  displayDefaults.set(viewportId, { voiRange, invert });
}

export function applyViewportAction(viewportId: string, action: ViewportAction): void {
  const viewport = getStackViewport(viewportId);
  if (!viewport) return;

  switch (action) {
    case 'reset': {
      // Not viewport.resetProperties(): on MONOCHROME1 images it restores the
      // un-inverted lookup table while still reporting invert=true, which
      // leaves the display inverted and the Invert button out of step.
      const defaults = displayDefaults.get(viewportId);
      if (defaults) viewport.setProperties(defaults);
      viewport.resetCamera();
      break;
    }
    case 'invert':
      viewport.setProperties({ invert: !viewport.getProperties().invert });
      break;
    case 'rotate': {
      const { rotation = 0 } = viewport.getViewPresentation();
      viewport.setViewPresentation({ rotation: (rotation + 90) % 360 });
      break;
    }
    case 'flip':
      viewport.setCamera({ flipHorizontal: !viewport.getCamera().flipHorizontal });
      break;
  }
  viewport.render();
}
