import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { PageCanvas } from '../../../components/documents/PageCanvas';
import { blobToBitmap, paintBitmap } from '../../../lib/pageBitmap';
import { ViewingProtectionContext } from '../../../lib/viewingProtection';

vi.mock('../../../lib/pageBitmap', () => ({
  blobToBitmap: vi.fn(),
  paintBitmap: vi.fn(),
}));
vi.mock('../../../services/api', () => ({ setViewerFocus: vi.fn() }));

let observerCallback;

describe('PageCanvas', () => {
  let scrollContainer;

  beforeEach(() => {
    vi.clearAllMocks();
    observerCallback = undefined;
    scrollContainer = document.createElement('div');
    vi.stubGlobal(
      'IntersectionObserver',
      vi.fn(function (callback) {
        observerCallback = callback;
        return { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() };
      })
    );
  });

  const bitmap = () => ({ width: 120, height: 200, close: vi.fn() });
  const setNear = (isIntersecting) => act(() => observerCallback([{ isIntersecting }]));

  it('does not fetch anything until the page is near the viewport', () => {
    const loadPage = vi.fn();
    render(<PageCanvas loadPage={loadPage} pageNumber={2} alt="Page 2" scrollContainer={scrollContainer} />);

    expect(loadPage).not.toHaveBeenCalled();
    expect(screen.getByText(/Loading page 2/)).toBeInTheDocument();
  });

  it('fetches the page bytes, paints them into the canvas and frees the bitmap', async () => {
    const decoded = bitmap();
    const blob = new Blob(['x']);
    const loadPage = vi.fn().mockResolvedValue(blob);
    blobToBitmap.mockResolvedValue(decoded);

    render(<PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} />);
    setNear(true);

    await waitFor(() => expect(paintBitmap).toHaveBeenCalledTimes(1));
    expect(loadPage).toHaveBeenCalledWith(1, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(blobToBitmap).toHaveBeenCalledWith(blob);
    const [canvasArg, bitmapArg] = paintBitmap.mock.calls[0];
    expect(canvasArg.tagName).toBe('CANVAS');
    expect(bitmapArg).toBe(decoded);
    await waitFor(() => expect(decoded.close).toHaveBeenCalled());
    expect(screen.queryByText(/Loading page 1/)).not.toBeInTheDocument();
  });

  it('never exposes the image as an <img> or any URL in the DOM', async () => {
    const loadPage = vi.fn().mockResolvedValue(new Blob(['x']));
    blobToBitmap.mockResolvedValue(bitmap());

    const { container } = render(
      <PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} />
    );
    setNear(true);
    await waitFor(() => expect(paintBitmap).toHaveBeenCalled());

    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('[src]')).toBeNull();
    expect(container.querySelector('canvas')).not.toBeNull();
  });

  it('blocks the context menu and dragging on the canvas', async () => {
    const loadPage = vi.fn().mockResolvedValue(new Blob(['x']));
    blobToBitmap.mockResolvedValue(bitmap());
    render(<PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} />);
    setNear(true);
    await waitFor(() => expect(paintBitmap).toHaveBeenCalled());

    const canvas = screen.getByRole('img', { name: 'Page 1' });
    expect(fireEvent.contextMenu(canvas)).toBe(false); // preventDefault() was called
    expect(fireEvent.dragStart(canvas)).toBe(false);
    expect(canvas.getAttribute('draggable')).toBe('false');
  });

  it('releases the pixel buffer when the page scrolls far away', async () => {
    const loadPage = vi.fn().mockResolvedValue(new Blob(['x']));
    blobToBitmap.mockResolvedValue(bitmap());
    paintBitmap.mockImplementation((canvas, bmp) => {
      canvas.width = bmp.width;
      canvas.height = bmp.height;
    });
    render(<PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} />);
    setNear(true);
    await waitFor(() => expect(paintBitmap).toHaveBeenCalled());
    const canvas = screen.getByRole('img', { name: 'Page 1' });
    expect(canvas.width).toBe(120);

    setNear(false);
    await waitFor(() => expect(canvas.width).toBe(1));
  });

  it('shows an inline retry when loading fails and retries on click', async () => {
    const loadPage = vi.fn().mockRejectedValueOnce(new Error('network')).mockResolvedValue(new Blob(['x']));
    blobToBitmap.mockResolvedValue(bitmap());
    render(<PageCanvas loadPage={loadPage} pageNumber={4} alt="Page 4" scrollContainer={scrollContainer} />);
    setNear(true);

    const retry = await screen.findByRole('button', { name: /Failed to load page 4/ });
    fireEvent.click(retry);

    await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(paintBitmap).toHaveBeenCalledTimes(1));
  });

  it('renders overlays (children) inside the page wrapper', () => {
    render(
      <PageCanvas loadPage={vi.fn()} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer}>
        <span data-testid="overlay">overlay</span>
      </PageCanvas>
    );
    expect(screen.getByTestId('page-canvas-1')).toContainElement(screen.getByTestId('overlay'));
  });

  it('reserves space using the page dimensions to avoid layout jumps', () => {
    render(
      <PageCanvas loadPage={vi.fn()} pageNumber={1} width={800} height={1000} alt="Page 1" scrollContainer={scrollContainer} />
    );
    const wrapper = screen.getByTestId('page-canvas-1');
    expect(wrapper.style.aspectRatio.replace(/\s/g, '')).toBe('800/1000');
    expect(wrapper.style.maxWidth).toBe('800px');
  });

  describe('while the viewer is suspended (not in focus)', () => {
    const renderWith = (suspended, loadPage, extra = {}) =>
      render(
        <ViewingProtectionContext.Provider value={{ suspended }}>
          <PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} {...extra} />
        </ViewingProtectionContext.Provider>
      );

    it('requests nothing', () => {
      const loadPage = vi.fn();
      renderWith(true, loadPage);
      setNear(true);
      expect(loadPage).not.toHaveBeenCalled();
    });

    it('empties the canvas it already painted and fetches again only after resuming', async () => {
      blobToBitmap.mockResolvedValue(bitmap());
      const loadPage = vi.fn().mockResolvedValue(new Blob(['x']));
      const { rerender } = renderWith(false, loadPage);
      setNear(true);
      await waitFor(() => expect(paintBitmap).toHaveBeenCalledTimes(1));
      const canvas = screen.getByRole('img');
      canvas.width = 120;
      rerender(
        <ViewingProtectionContext.Provider value={{ suspended: true }}>
          <PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} />
        </ViewingProtectionContext.Provider>
      );
      await waitFor(() => expect(canvas.width).toBe(1));
      rerender(
        <ViewingProtectionContext.Provider value={{ suspended: false }}>
          <PageCanvas loadPage={loadPage} pageNumber={1} alt="Page 1" scrollContainer={scrollContainer} />
        </ViewingProtectionContext.Provider>
      );
      await waitFor(() => expect(loadPage).toHaveBeenCalledTimes(2));
    });
  });

  it('adds the spotlight layer only when the page is ready and a spotlight loader is given', async () => {
    blobToBitmap.mockResolvedValue(bitmap());
    const loadPage = vi.fn().mockResolvedValue(new Blob(['x']));
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: vi.fn(), clearRect: vi.fn() }));
    render(<PageCanvas loadPage={loadPage} loadTiles={vi.fn()} pageNumber={3} alt="Page 3" scrollContainer={scrollContainer} />);
    expect(screen.queryByTestId('spotlight-3')).toBeNull();
    setNear(true);
    await waitFor(() => expect(screen.getByTestId('spotlight-3')).toBeInTheDocument());
  });
});
