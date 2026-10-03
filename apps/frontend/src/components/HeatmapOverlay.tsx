import { Enums, utilities } from '@cornerstonejs/core';
import { useEffect, useRef, useState } from 'react';

import { api } from '../lib/api/client';
import { imageCornerIndices, overlayTransform, type Point } from '../lib/cornerstone/heatmapTransform';
import { getStackViewport } from '../lib/cornerstone/viewer';

interface Props {
  viewportId: string;
  /** The Cornerstone-enabled element this overlay sits on top of. */
  element: HTMLDivElement;
  /** Backend URL of the heatmap PNG, or null to show nothing. */
  heatmapUrl: string | null;
  opacity: number;
}

/**
 * Toggleable Grad-CAM layer. A transparent canvas above the viewport is
 * redrawn after every Cornerstone render so the heatmap tracks pan, zoom,
 * rotation and flips exactly.
 */
export function HeatmapOverlay({ viewportId, element, heatmapUrl, opacity }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);

  // Fetch the PNG (with credentials) and decode it.
  useEffect(() => {
    setImage(null);
    if (!heatmapUrl) return;
    let objectUrl: string | null = null;
    let cancelled = false;

    api
      .heatmap(heatmapUrl)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        const decoded = new Image();
        decoded.onload = () => !cancelled && setImage(decoded);
        decoded.src = objectUrl;
      })
      .catch(() => {
        // A missing overlay is non-fatal: the findings list still shows the result.
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [heatmapUrl]);

  useEffect(() => {
    const draw = () => {
      const canvas = canvasRef.current;
      const context = canvas?.getContext('2d');
      if (!canvas || !context) return;

      const ratio = window.devicePixelRatio || 1;
      const width = Math.round(element.clientWidth * ratio);
      const height = Math.round(element.clientHeight * ratio);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.setTransform(1, 0, 0, 1, 0, 0);
      context.clearRect(0, 0, canvas.width, canvas.height);
      if (!image) return;

      const viewport = getStackViewport(viewportId);
      const imageId = viewport?.getCurrentImageId();
      const dimensions = viewport?.getImageData()?.dimensions;
      if (!viewport || !imageId || !dimensions) return;

      const toCanvas = (index: Point): Point | null => {
        const world = utilities.imageToWorldCoords(imageId, [index[0], index[1]]);
        if (!world) return null;
        const [x, y] = viewport.worldToCanvas(world);
        return [x, y];
      };
      const indices = imageCornerIndices(dimensions[0], dimensions[1]);
      const topLeft = toCanvas(indices.topLeft);
      const topRight = toCanvas(indices.topRight);
      const bottomLeft = toCanvas(indices.bottomLeft);
      if (!topLeft || !topRight || !bottomLeft) return;

      context.setTransform(
        ...overlayTransform(
          { topLeft, topRight, bottomLeft },
          image.naturalWidth,
          image.naturalHeight,
          ratio,
        ),
      );
      context.globalAlpha = opacity;
      context.imageSmoothingEnabled = true;
      context.drawImage(image, 0, 0);
    };

    draw();
    element.addEventListener(Enums.Events.IMAGE_RENDERED, draw);
    return () => element.removeEventListener(Enums.Events.IMAGE_RENDERED, draw);
  }, [element, viewportId, image, opacity]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden
      className="pointer-events-none absolute inset-0 h-full w-full"
    />
  );
}
