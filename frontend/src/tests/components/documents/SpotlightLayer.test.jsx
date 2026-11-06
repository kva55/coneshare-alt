import { act, render } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  cellsForCircle,
  MAX_CACHED_TILES,
  SpotlightLayer,
  tileSizeFor,
} from '../../../components/documents/SpotlightLayer';

vi.mock('../../../lib/pageBitmap', () => ({
  blobToBitmap: vi.fn(async () => ({ width: 10, height: 10, close: vi.fn() })),
}));

const PAGE = { width: 1000, height: 1300 }; // tile = 80px
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });

function setup(loadTiles) {
  const wrapper = document.createElement('div');
  wrapper.getBoundingClientRect = () => ({ left: 0, top: 0, width: 500, height: 650 }); // half scale
  document.body.appendChild(wrapper);
  const wrapperRef = createRef();
  wrapperRef.current = wrapper;
  const calls = [];
  const ctx = {
    drawImage: vi.fn(), clearRect: vi.fn(), fillRect: vi.fn(),
    createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  };
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ctx);
  const utils = render(<SpotlightLayer loadTiles={loadTiles} pageNumber={2} pageSize={PAGE} wrapperRef={wrapperRef} />);
  return { wrapper, ctx, calls, ...utils };
}

const answer = (cells) => ({ tileSize: 80, tiles: cells.map(([c, r]) => ({ c, r, x: c * 80, y: r * 80, blob: new Blob(['x']) })) });
const move = async (wrapper, x, y) => { act(() => { wrapper.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y })); }); await flush(); };

describe('cellsForCircle', () => {
  it('lists the grid cells a circle touches, clipped to the page', () => {
    expect(tileSizeFor(1000)).toBe(80);
    const cells = cellsForCircle(500, 650, 100, 1000, 1300);
    expect(cells.length).toBeGreaterThanOrEqual(9);
    expect(cells.every(([c, r]) => c >= 0 && r >= 0 && c < 13 && r < 17)).toBe(true);
    expect(cellsForCircle(0, 0, 100, 1000, 1300).every(([c, r]) => c >= 0 && r >= 0)).toBe(true);
  });
});

describe('SpotlightLayer', () => {
  beforeEach(() => { vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 1; }); vi.stubGlobal('cancelAnimationFrame', vi.fn()); });
  afterEach(() => { vi.unstubAllGlobals(); document.body.innerHTML = ''; });

  it('asks only for the cells under the cursor, in one request, and draws them', async () => {
    const load = vi.fn(async (_p, { tiles }) => answer(tiles));
    const { wrapper, ctx } = setup(load);
    await move(wrapper, 250, 325); // page px (500, 650)
    expect(load).toHaveBeenCalledTimes(1);
    const asked = load.mock.calls[0][1].tiles;
    expect(asked.length).toBe(cellsForCircle(500, 650, 100, 1000, 1300).length);
    expect(ctx.drawImage).toHaveBeenCalled();
  });

  it('does not ask again for tiles it already holds', async () => {
    const load = vi.fn(async (_p, { tiles }) => answer(tiles));
    const { wrapper } = setup(load);
    await move(wrapper, 250, 325);
    await move(wrapper, 252, 326);
    await move(wrapper, 248, 322);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('asks only for the new cells after a move', async () => {
    const load = vi.fn(async (_p, { tiles }) => answer(tiles));
    const { wrapper } = setup(load);
    await move(wrapper, 250, 325);
    const first = load.mock.calls[0][1].tiles.length;
    await move(wrapper, 290, 325); // 80 page-px to the right: one new column
    expect(load).toHaveBeenCalledTimes(2);
    const second = load.mock.calls[1][1].tiles;
    expect(second.length).toBeLessThan(first);
    expect(second.length).toBeGreaterThan(0);
  });

  it('does not ask twice for a tile that is still in flight', async () => {
    let release;
    const load = vi.fn(() => new Promise((resolve) => { release = resolve; }));
    const { wrapper } = setup(load);
    await move(wrapper, 250, 325);
    await move(wrapper, 251, 325);
    expect(load).toHaveBeenCalledTimes(1);
    release({ tiles: [] });
  });

  it('keeps the page blurred when the server refuses', async () => {
    const load = vi.fn(async () => { throw new Error('429'); });
    const { wrapper, ctx } = setup(load);
    await move(wrapper, 250, 325);
    expect(ctx.drawImage).not.toHaveBeenCalled();
  });

  it('keeps a bounded number of tiles', () => {
    expect(MAX_CACHED_TILES).toBeLessThanOrEqual(64);
  });

  it('erases the circle when the cursor leaves the page', async () => {
    const load = vi.fn(async (_p, { tiles }) => answer(tiles));
    const { wrapper, ctx } = setup(load);
    await move(wrapper, 250, 325);
    ctx.drawImage.mockClear();
    act(() => { wrapper.dispatchEvent(new Event('mouseleave')); });
    await flush();
    expect(ctx.drawImage).not.toHaveBeenCalled();
  });

  it('never intercepts pointer events', () => {
    const { getByTestId } = setup(vi.fn());
    expect(getByTestId('spotlight-2').className).toContain('pointer-events-none');
  });
});
