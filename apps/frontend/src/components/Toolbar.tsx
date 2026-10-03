import {
  Contrast,
  FlipHorizontal2,
  Grid2x2,
  Hand,
  RotateCcw,
  RotateCw,
  Ruler,
  Square,
  SquareSplitHorizontal,
  SunMoon,
  Triangle,
  ZoomIn,
  type LucideIcon,
} from 'lucide-react';

import { applyViewportAction, type PrimaryTool, type ViewportAction } from '../lib/cornerstone/viewer';
import { useViewerStore, viewportIdForSlot, type Layout } from '../state/viewerStore';

const TOOLS: Array<{ id: PrimaryTool; label: string; icon: LucideIcon }> = [
  { id: 'windowLevel', label: 'Window / Level', icon: Contrast },
  { id: 'pan', label: 'Pan', icon: Hand },
  { id: 'zoom', label: 'Zoom', icon: ZoomIn },
  { id: 'length', label: 'Length', icon: Ruler },
  { id: 'angle', label: 'Angle', icon: Triangle },
];

const ACTIONS: Array<{ id: ViewportAction; label: string; icon: LucideIcon }> = [
  { id: 'invert', label: 'Invert', icon: SunMoon },
  { id: 'rotate', label: 'Rotate 90°', icon: RotateCw },
  { id: 'flip', label: 'Flip horizontal', icon: FlipHorizontal2 },
  { id: 'reset', label: 'Reset view', icon: RotateCcw },
];

const LAYOUT_OPTIONS: Array<{ id: Layout; label: string; icon: LucideIcon }> = [
  { id: '1x1', label: 'Single viewport', icon: Square },
  { id: '1x2', label: 'Side by side', icon: SquareSplitHorizontal },
  { id: '2x2', label: '2 × 2 grid', icon: Grid2x2 },
];

function ToolButton({
  label,
  icon: Icon,
  pressed,
  onClick,
}: {
  label: string;
  icon: LucideIcon;
  pressed?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      aria-pressed={pressed}
      onClick={onClick}
      className={`flex h-8 w-8 items-center justify-center rounded transition-colors ${
        pressed
          ? 'bg-accent/15 text-accent'
          : 'text-ink-dim hover:bg-surface-3 hover:text-ink'
      }`}
    >
      <Icon size={16} strokeWidth={1.75} />
    </button>
  );
}

const Divider = () => <span className="mx-1 h-5 w-px bg-line" aria-hidden />;

export function Toolbar() {
  const primaryTool = useViewerStore((state) => state.primaryTool);
  const setPrimaryTool = useViewerStore((state) => state.setPrimaryTool);
  const layout = useViewerStore((state) => state.layout);
  const setLayout = useViewerStore((state) => state.setLayout);
  const activeSlot = useViewerStore((state) => state.activeSlot);

  return (
    <div
      role="toolbar"
      aria-label="Viewer tools"
      className="flex h-10 shrink-0 items-center gap-0.5 border-b border-line bg-surface-1 px-2"
    >
      {TOOLS.map((tool) => (
        <ToolButton
          key={tool.id}
          label={tool.label}
          icon={tool.icon}
          pressed={primaryTool === tool.id}
          onClick={() => setPrimaryTool(tool.id)}
        />
      ))}
      <Divider />
      {ACTIONS.map((action) => (
        <ToolButton
          key={action.id}
          label={action.label}
          icon={action.icon}
          onClick={() => applyViewportAction(viewportIdForSlot(activeSlot), action.id)}
        />
      ))}
      <Divider />
      {LAYOUT_OPTIONS.map((option) => (
        <ToolButton
          key={option.id}
          label={option.label}
          icon={option.icon}
          pressed={layout === option.id}
          onClick={() => setLayout(option.id)}
        />
      ))}
      <span className="ml-auto hidden truncate pl-3 text-[11px] text-ink-faint xl:block">
        Left: selected tool · Middle: pan · Right: zoom · Wheel: scroll
      </span>
    </div>
  );
}
