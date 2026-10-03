/**
 * Geometry for drawing an AI heatmap over a Cornerstone viewport.
 *
 * The heatmap PNG spans the full source image. To keep it locked to the image
 * through pan, zoom, rotation and flips, we ask Cornerstone where three image
 * corners currently land on the canvas and derive the affine transform that
 * maps heatmap pixels onto them.
 */

export type Point = readonly [number, number];

/** Canvas positions (CSS pixels) of three corners of the displayed image. */
export interface ImageCorners {
  topLeft: Point;
  topRight: Point;
  bottomLeft: Point;
}

/** Arguments for `CanvasRenderingContext2D.setTransform(a, b, c, d, e, f)`. */
export type CanvasTransform = [a: number, b: number, c: number, d: number, e: number, f: number];

/**
 * Transform mapping overlay pixel (u, v) to device pixels, so that the overlay
 * `(0,0)-(width,height)` rectangle exactly covers the image.
 */
export function overlayTransform(
  corners: ImageCorners,
  overlayWidth: number,
  overlayHeight: number,
  devicePixelRatio = 1,
): CanvasTransform {
  const [x0, y0] = corners.topLeft;
  // Canvas displacement per overlay pixel along each overlay axis.
  const ux = (corners.topRight[0] - x0) / overlayWidth;
  const uy = (corners.topRight[1] - y0) / overlayWidth;
  const vx = (corners.bottomLeft[0] - x0) / overlayHeight;
  const vy = (corners.bottomLeft[1] - y0) / overlayHeight;
  const k = devicePixelRatio;
  return [ux * k, uy * k, vx * k, vy * k, x0 * k, y0 * k];
}

/**
 * Pixel-index coordinates of the image's outer corners. Pixel (0, 0) is the
 * *centre* of the first pixel, so the image edge sits half a pixel outside.
 */
export function imageCornerIndices(columns: number, rows: number) {
  return {
    topLeft: [-0.5, -0.5] as Point,
    topRight: [columns - 0.5, -0.5] as Point,
    bottomLeft: [-0.5, rows - 0.5] as Point,
  };
}
