import { describe, expect, it } from 'vitest';

import { imageCornerIndices, overlayTransform, type CanvasTransform } from './heatmapTransform';

/** Apply a canvas transform to an overlay pixel. */
const apply = ([a, b, c, d, e, f]: CanvasTransform, u: number, v: number) => [
  a * u + c * v + e,
  b * u + d * v + f,
];

describe('overlayTransform', () => {
  it('scales and translates an unrotated image', () => {
    // A 512x256 overlay over an image shown at (100,50)-(500,250).
    const transform = overlayTransform(
      { topLeft: [100, 50], topRight: [500, 50], bottomLeft: [100, 250] },
      512,
      256,
    );
    expect(apply(transform, 0, 0)).toEqual([100, 50]);
    expect(apply(transform, 512, 0)).toEqual([500, 50]);
    expect(apply(transform, 0, 256)).toEqual([100, 250]);
    expect(apply(transform, 512, 256)).toEqual([500, 250]);
  });

  it('follows a 90 degree rotation', () => {
    // Image rotated clockwise: its top edge now runs down the right-hand side.
    const transform = overlayTransform(
      { topLeft: [300, 0], topRight: [300, 200], bottomLeft: [100, 0] },
      100,
      100,
    );
    expect(apply(transform, 100, 0)).toEqual([300, 200]);
    expect(apply(transform, 0, 100)).toEqual([100, 0]);
    expect(apply(transform, 100, 100)).toEqual([100, 200]);
  });

  it('follows a horizontal flip', () => {
    const transform = overlayTransform(
      { topLeft: [400, 0], topRight: [0, 0], bottomLeft: [400, 300] },
      200,
      150,
    );
    expect(apply(transform, 0, 0)).toEqual([400, 0]);
    expect(apply(transform, 200, 150)).toEqual([0, 300]);
  });

  it('accounts for the device pixel ratio', () => {
    const transform = overlayTransform(
      { topLeft: [10, 20], topRight: [110, 20], bottomLeft: [10, 120] },
      100,
      100,
      2,
    );
    expect(apply(transform, 0, 0)).toEqual([20, 40]);
    expect(apply(transform, 100, 100)).toEqual([220, 240]);
  });
});

describe('imageCornerIndices', () => {
  it('places corners half a pixel outside the first and last pixel centres', () => {
    expect(imageCornerIndices(96, 64)).toEqual({
      topLeft: [-0.5, -0.5],
      topRight: [95.5, -0.5],
      bottomLeft: [-0.5, 63.5],
    });
  });
});
