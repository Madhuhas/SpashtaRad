import { LAYOUTS, useViewerStore } from '../state/viewerStore';
import { ViewportPane } from './ViewportPane';

/** Multi-viewport grid. Panes are keyed by slot so switching layout keeps their state. */
export function ViewportGrid() {
  const layout = useViewerStore((state) => state.layout);
  const { slots, columns, rows } = LAYOUTS[layout];

  return (
    <div
      className="grid min-h-0 flex-1 gap-px bg-surface-0 p-px"
      style={{
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
      }}
    >
      {Array.from({ length: slots }, (_, slot) => (
        <ViewportPane key={slot} slot={slot} />
      ))}
    </div>
  );
}
