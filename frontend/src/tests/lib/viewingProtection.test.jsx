import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useFocusLease } from '../../lib/viewingProtection';
import * as api from '../../services/api';

vi.mock('../../services/api', () => ({ setViewerFocus: vi.fn() }));

describe('useFocusLease', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.setViewerFocus.mockResolvedValue({});
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  });
  afterEach(() => vi.restoreAllMocks());

  it('does nothing and never suspends when the link does not use it', () => {
    const { result } = renderHook(() => useFocusLease('s', false));
    expect(result.current.suspended).toBe(false);
    expect(api.setViewerFocus).not.toHaveBeenCalled();
  });

  it('stays suspended until the server has acknowledged the focus', async () => {
    let release;
    api.setViewerFocus.mockReturnValue(new Promise((resolve) => { release = resolve; }));
    const { result } = renderHook(() => useFocusLease('s', true));
    expect(result.current.suspended).toBe(true);
    await act(async () => { release({}); });
    await waitFor(() => expect(result.current.suspended).toBe(false));
    expect(api.setViewerFocus).toHaveBeenCalledWith('s', true);
  });

  it('tells the server and suspends when the window loses focus', async () => {
    const { result } = renderHook(() => useFocusLease('s', true));
    await waitFor(() => expect(result.current.suspended).toBe(false));
    document.hasFocus.mockReturnValue(false);
    await act(async () => { window.dispatchEvent(new Event('blur')); });
    expect(api.setViewerFocus).toHaveBeenLastCalledWith('s', false);
    expect(result.current.suspended).toBe(true);
    expect(result.current.focused).toBe(false);
  });

  it('resumes when focus returns', async () => {
    const { result } = renderHook(() => useFocusLease('s', true));
    document.hasFocus.mockReturnValue(false);
    await act(async () => { window.dispatchEvent(new Event('blur')); });
    document.hasFocus.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    await waitFor(() => expect(result.current.suspended).toBe(false));
    expect(api.setViewerFocus).toHaveBeenLastCalledWith('s', true);
  });

  it('suspends when the tab becomes hidden', async () => {
    const { result } = renderHook(() => useFocusLease('s', true));
    await waitFor(() => expect(result.current.suspended).toBe(false));
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(result.current.suspended).toBe(true);
  });

  it('stays suspended if the server cannot be reached', async () => {
    api.setViewerFocus.mockRejectedValue(new Error('down'));
    const { result } = renderHook(() => useFocusLease('s', true));
    await act(async () => { await Promise.resolve(); });
    expect(result.current.suspended).toBe(true);
  });

  it('sends a heartbeat so the lease does not lapse', async () => {
    vi.useFakeTimers();
    renderHook(() => useFocusLease('s', true));
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(api.setViewerFocus.mock.calls.length).toBeGreaterThanOrEqual(2);
    vi.useRealTimers();
  });
});
