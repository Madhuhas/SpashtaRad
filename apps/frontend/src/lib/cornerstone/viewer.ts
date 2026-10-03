/**
 * Cornerstone3D bootstrap: one rendering engine and one tool group for the
 * whole app. Components call `initViewer()` and await it before touching
 * any Cornerstone API.
 */
import {
  init as initCore,
  RenderingEngine,
  type Types,
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

function setUpTools(): void {
  for (const tool of [WindowLevelTool, PanTool, ZoomTool, StackScrollTool, LengthTool, AngleTool]) {
    addTool(tool);
  }
  const group = ToolGroupManager.createToolGroup(TOOL_GROUP_ID);
  if (!group) throw new Error('Unable to create Cornerstone tool group');

  for (const toolName of [...Object.values(PRIMARY_TOOLS), StackScrollTool.toolName]) {
    group.addTool(toolName);
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

/** Initialise Cornerstone once. Safe to call from every component. */
export function initViewer(): Promise<void> {
  ready ??= (async () => {
    await initCore();
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
  const secondaryBindings: Partial<Record<PrimaryTool, number>> = {
    pan: MouseBindings.Auxiliary,
    zoom: MouseBindings.Secondary,
  };

  // Release the outgoing tool, keeping its fixed middle/right binding if it has one.
  const previousName = PRIMARY_TOOLS[previous];
  const keep = secondaryBindings[previous];
  if (keep !== undefined) {
    group.setToolActive(previousName, { bindings: [{ mouseButton: keep }] });
  } else {
    group.setToolPassive(previousName);
  }

  const bindings = [{ mouseButton: MouseBindings.Primary as number }];
  const extra = secondaryBindings[tool];
  if (extra !== undefined) bindings.push({ mouseButton: extra });
  group.setToolActive(PRIMARY_TOOLS[tool], { bindings });
}

export function getStackViewport(viewportId: string): Types.IStackViewport | undefined {
  return engine?.getViewport<Types.IStackViewport>(viewportId) ?? undefined;
}

export type ViewportAction = 'reset' | 'invert' | 'rotate' | 'flip';

export function applyViewportAction(viewportId: string, action: ViewportAction): void {
  const viewport = getStackViewport(viewportId);
  if (!viewport) return;

  switch (action) {
    case 'reset':
      viewport.resetProperties();
      viewport.resetCamera();
      break;
    case 'invert':
      viewport.setProperties({ invert: !viewport.getProperties().invert });
      break;
    case 'rotate': {
      const { rotation = 0 } = viewport.getViewPresentation({ rotation: true });
      viewport.setViewPresentation({ rotation: (rotation + 90) % 360 });
      break;
    }
    case 'flip': {
      const { flipHorizontal = false } = viewport.getViewPresentation({ flipHorizontal: true });
      viewport.setViewPresentation({ flipHorizontal: !flipHorizontal });
      break;
    }
  }
  viewport.render();
}
