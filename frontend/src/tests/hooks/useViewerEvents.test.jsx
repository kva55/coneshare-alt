import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCaptureResponse, useCheckoutViewerEvents, useViewerEvents } from '../../hooks/useViewerEvents';
import * as api from '../../services/api';

vi.mock('../../services/api', () => ({
  reportViewerEvent: vi.fn(), beaconViewerEvent: vi.fn(), reportCheckoutEvent: vi.fn(), beaconCheckoutEvent: vi.fn(),
}));

const press = (init) => act(async () => {
  window.dispatchEvent(new KeyboardEvent('keydown', init));
  await Promise.resolve();
});

describe('event reporting', () => {
  beforeEach(() => { vi.clearAllMocks(); api.reportViewerEvent.mockResolvedValue({}); api.reportCheckoutEvent.mockResolvedValue({}); });
  afterEach(() => vi.useRealTimers());

  it('reports share-link signals against the link', async () => {
    renderHook(() => useViewerEvents('slug-1', true));
    await press({ key: 's', code: 'KeyS', ctrlKey: true });
    expect(api.reportViewerEvent).toHaveBeenCalledWith('slug-1', 'save_attempt', {});
  });

  it('reports check-out signals against the check-out', async () => {
    renderHook(() => useCheckoutViewerEvents('c1', true));
    await press({ key: 'p', code: 'KeyP', ctrlKey: true });
    expect(api.reportCheckoutEvent).toHaveBeenCalledWith('c1', 'print_attempt', { via: 'shortcut' });
    expect(api.reportViewerEvent).not.toHaveBeenCalled();
  });

  it('does nothing while disabled', async () => {
    renderHook(() => useCheckoutViewerEvents('c1', false));
    await press({ key: 'p', code: 'KeyP', ctrlKey: true });
    expect(api.reportCheckoutEvent).not.toHaveBeenCalled();
  });

  it('retries a failed delivery instead of losing the event', async () => {
    vi.useFakeTimers();
    api.reportCheckoutEvent.mockRejectedValueOnce(new Error('net')).mockRejectedValueOnce(new Error('net')).mockResolvedValue({});
    renderHook(() => useCheckoutViewerEvents('c1', true));
    await press({ key: 'F12', code: 'F12' });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.reportCheckoutEvent).toHaveBeenCalledTimes(3);
  });

  it('keeps events in order and sends them one after another', async () => {
    const order = [];
    api.reportCheckoutEvent.mockImplementation(async (id, type) => { order.push(type); });
    renderHook(() => useCheckoutViewerEvents('c1', true));
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', code: 'KeyS', ctrlKey: true }));
      document.dispatchEvent(new Event('copy'));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F12', code: 'F12' }));
      await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(order).toEqual(['save_attempt', 'copy_attempt', 'devtools_key']);
  });

  it('hands queued events to the beacon when the page goes away', async () => {
    let release;
    api.reportCheckoutEvent.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const { unmount } = renderHook(() => useCheckoutViewerEvents('c1', true));
    await press({ key: 's', code: 'KeyS', ctrlKey: true });
    await press({ key: 'F12', code: 'F12' });          // still queued: the first send has not finished
    act(() => { window.dispatchEvent(new Event('pagehide')); });
    expect(api.beaconCheckoutEvent).toHaveBeenCalledWith('c1', 'devtools_key', { key: 'F12' });
    release({});
    unmount();
  });

  it('passes the configured actions on and lets the page react', async () => {
    const { result } = renderHook(() => {
      const capture = useCaptureResponse();
      useCheckoutViewerEvents('c1', true, { actions: { print_attempt: 'blur', save_attempt: 'end', copy_attempt: 'ignore' }, onAction: capture.onAction });
      return capture;
    });
    expect(result.current.blurred).toBe(false);
    await press({ key: 'p', code: 'KeyP', ctrlKey: true });
    expect(result.current.blurred).toBe(true);
    act(() => result.current.resume());
    expect(result.current.blurred).toBe(false);
    document.dispatchEvent(new Event('copy'));                      // ignored: never reported
    await press({ key: 's', code: 'KeyS', ctrlKey: true });
    expect(result.current.ended).toBe(true);
    expect(api.reportCheckoutEvent.mock.calls.map((c) => c[1])).toEqual(['print_attempt', 'save_attempt']);
  });
});
