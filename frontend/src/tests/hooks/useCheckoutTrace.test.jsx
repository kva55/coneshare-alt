import { render, act } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { useCheckoutTrace } from '../../hooks/useCheckoutTrace';
import { sendCheckoutTrace } from '../../services/api';

vi.mock('../../services/api', () => ({ sendCheckoutTrace: vi.fn(() => Promise.resolve()) }));

function Harness({ enabled = true }) {
  const [node, setNode] = useState(null);
  useCheckoutTrace('c1', enabled, node);
  return (
    <div ref={setNode} data-testid="scroll">
      <div data-page-number="2" data-testid="page" />
    </div>
  );
}

const box = (el, r) => { el.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 400, right: 200, bottom: 400, ...r }); };

describe('useCheckoutTrace', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.spyOn(document, 'hasFocus').mockReturnValue(true);
  });
  afterEach(() => vi.useRealTimers());

  it('sends cursor positions as fractions of the page under the cursor', () => {
    const { getByTestId, unmount } = render(<Harness />);
    const page = getByTestId('page');
    box(page);
    act(() => { vi.advanceTimersByTime(200); });
    act(() => { page.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 100, clientY: 100 })); });
    unmount();
    expect(sendCheckoutTrace).toHaveBeenCalledTimes(1);
    const [id, body] = sendCheckoutTrace.mock.calls[0];
    expect(id).toBe('c1');
    expect(body.moves).toHaveLength(1);
    expect(body.moves[0].slice(1)).toEqual([2, 500, 250]);
  });

  it('samples at most every 100 ms', () => {
    const { getByTestId, unmount } = render(<Harness />);
    const page = getByTestId('page');
    box(page);
    act(() => { vi.advanceTimersByTime(300); });
    for (let i = 0; i < 5; i += 1) {
      act(() => { page.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 10 * i, clientY: 10 })); });
    }
    unmount();
    expect(sendCheckoutTrace.mock.calls[0][1].moves).toHaveLength(1);
  });

  it('records the page at the centre of the screen once a second', () => {
    const { getByTestId, unmount } = render(<Harness />);
    box(getByTestId('page'), { top: -100, bottom: 300 });
    box(getByTestId('scroll'), { top: 0, height: 400 });
    act(() => { vi.advanceTimersByTime(1100); });
    unmount();
    expect(sendCheckoutTrace.mock.calls[0][1].views[0].slice(1)).toEqual([2, 750]);
  });

  it('records left-button down and up as clicks, with the page position and the sending clock', () => {
    const { getByTestId, unmount } = render(<Harness />);
    const page = getByTestId('page');
    box(page);
    act(() => { vi.advanceTimersByTime(200); });
    act(() => { page.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0, clientX: 100, clientY: 100 })); });
    act(() => { page.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0, clientX: 150, clientY: 200 })); });
    act(() => { page.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 2, clientX: 5, clientY: 5 })); });   // right button: ignored
    unmount();
    const body = sendCheckoutTrace.mock.calls[0][1];
    expect(body.clicks.map((c) => [c[1], c[2], c[3], c[4]])).toEqual([[2, 500, 250, 0], [2, 750, 500, 1]]);
    expect(Number.isInteger(body.now)).toBe(true);
  });

  it('does nothing when the server has tracing switched off', () => {
    const { getByTestId, unmount } = render(<Harness enabled={false} />);
    box(getByTestId('page'));
    act(() => { vi.advanceTimersByTime(2000); });
    getByTestId('page').dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 10, clientY: 10 }));
    unmount();
    expect(sendCheckoutTrace).not.toHaveBeenCalled();
  });

  it('does not record while the window is not focused', () => {
    document.hasFocus.mockReturnValue(false);
    const { getByTestId, unmount } = render(<Harness />);
    box(getByTestId('page'));
    act(() => { vi.advanceTimersByTime(500); });
    getByTestId('page').dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 10, clientY: 10 }));
    unmount();
    expect(sendCheckoutTrace).not.toHaveBeenCalled();
  });
});
