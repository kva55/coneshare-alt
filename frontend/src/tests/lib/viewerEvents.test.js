import { afterEach, describe, expect, it, vi } from 'vitest';
import { DETECTORS, startViewerEvents } from '../../lib/viewerEvents';

describe('viewerEvents', () => {
  let stop;
  afterEach(() => stop?.());

  it('reports print via Ctrl+P and the beforeprint event', () => {
    const report = vi.fn();
    stop = startViewerEvents(report, { minIntervalMs: 0 });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', ctrlKey: true }));
    window.dispatchEvent(new Event('beforeprint'));
    expect(report).toHaveBeenCalledWith('print_attempt', { via: 'shortcut' });
    expect(report).toHaveBeenCalledWith('print_attempt', { via: 'beforeprint' });
  });

  it('reports PrintScreen, copy and context menu', () => {
    const report = vi.fn();
    stop = startViewerEvents(report, { minIntervalMs: 0 });
    window.dispatchEvent(new KeyboardEvent('keyup', { key: 'PrintScreen' }));
    document.dispatchEvent(new Event('copy'));
    document.dispatchEvent(new Event('contextmenu'));
    const types = report.mock.calls.map((c) => c[0]);
    expect(types).toEqual(expect.arrayContaining(['screenshot_key', 'copy_attempt', 'context_menu']));
  });

  it('reports saving the page and inspecting it', () => {
    const report = vi.fn();
    stop = startViewerEvents(report, { minIntervalMs: 0 });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F12' }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'I', ctrlKey: true, shiftKey: true }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'u', ctrlKey: true }));
    const types = report.mock.calls.map((c) => c[0]);
    expect(types.filter((t) => t === 'save_attempt')).toHaveLength(1);
    expect(types.filter((t) => t === 'devtools_key')).toHaveLength(3);
  });

  it('reports losing window focus', () => {
    const report = vi.fn();
    stop = startViewerEvents(report, { minIntervalMs: 0 });
    window.dispatchEvent(new Event('blur'));
    expect(report).toHaveBeenCalledWith('window_blur', {});
  });

  it('does not treat ordinary keys as events', () => {
    const report = vi.fn();
    stop = startViewerEvents(report, { minIntervalMs: 0 });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's' }));
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', ctrlKey: true }));
    expect(report).not.toHaveBeenCalled();
  });

  it('throttles repeats of the same type', () => {
    const report = vi.fn();
    let t = 0;
    stop = startViewerEvents(report, { minIntervalMs: 1000, now: () => t });
    document.dispatchEvent(new Event('copy'));
    t = 500;
    document.dispatchEvent(new Event('copy'));
    t = 1500;
    document.dispatchEvent(new Event('copy'));
    expect(report).toHaveBeenCalledTimes(2);
  });

  it('stops listening once stopped', () => {
    const report = vi.fn();
    startViewerEvents(report, { minIntervalMs: 0 })();
    document.dispatchEvent(new Event('copy'));
    expect(report).not.toHaveBeenCalled();
  });

  it('is extensible: a custom detector can be supplied without touching others', () => {
    const report = vi.fn();
    const custom = { id: 'x', eventType: 'custom_event', attach: (emit) => { emit({ a: 1 }); return () => {}; } };
    stop = startViewerEvents(report, { detectors: [custom] });
    expect(report).toHaveBeenCalledWith('custom_event', { a: 1 });
    expect(DETECTORS.length).toBeGreaterThan(3);
  });

  describe('reliability', () => {
    it('recognises shortcuts by physical key, so layout and Caps Lock do not hide them', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0 });
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'ß', code: 'KeyS', ctrlKey: true }));   // another layout
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'S', code: 'KeyS', metaKey: true }));   // Caps Lock, Cmd
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'п', code: 'KeyP', ctrlKey: true }));   // Cyrillic layout
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'г', code: 'KeyU', ctrlKey: true }));
      const types = report.mock.calls.map((c) => c[0]);
      expect(types.filter((t) => t === 'save_attempt')).toHaveLength(2);
      expect(types).toContain('print_attempt');
      expect(types).toContain('devtools_key');
    });

    it('catches PrintScreen however the browser names it, on key down or key up', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0 });
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'Snapshot' }));
      window.dispatchEvent(new KeyboardEvent('keyup', { code: 'PrintScreen' }));
      window.dispatchEvent(new KeyboardEvent('keyup', { keyCode: 44 }));
      expect(report.mock.calls.filter((c) => c[0] === 'screenshot_key')).toHaveLength(3);
    });

    it('merges a key down/up pair into one event but keeps separate presses', () => {
      const report = vi.fn();
      let t = 0;
      stop = startViewerEvents(report, { now: () => t });
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'PrintScreen' }));
      t = 40;
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'PrintScreen' }));
      expect(report).toHaveBeenCalledTimes(1);
      t = 900;
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'PrintScreen' }));
      expect(report).toHaveBeenCalledTimes(2);
    });

    it('keeps every right-click, even in quick succession, and sees them in the capture phase', () => {
      const report = vi.fn();
      let t = 0;
      stop = startViewerEvents(report, { now: () => t });
      const blocker = document.createElement('div');
      blocker.addEventListener('contextmenu', (e) => e.stopPropagation());   // the page tries to swallow it
      document.body.appendChild(blocker);
      for (const time of [0, 600, 1200]) {
        t = time;
        blocker.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
      }
      expect(report.mock.calls.filter((c) => c[0] === 'context_menu')).toHaveLength(3);
      blocker.remove();
    });

    it('reports cut as a copy attempt, and dragging', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0 });
      document.dispatchEvent(new Event('cut'));
      document.dispatchEvent(new Event('dragstart', { bubbles: true }));
      expect(report).toHaveBeenCalledWith('copy_attempt', { via: 'cut' });
      expect(report.mock.calls.map((c) => c[0])).toContain('drag_attempt');
    });

    it('records which modifiers were held and whether only the key release was seen', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0 });
      window.dispatchEvent(new KeyboardEvent('keyup', { key: 'PrintScreen' }));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'PrintScreen', altKey: true }));
      const calls = report.mock.calls.filter((c) => c[0] === 'screenshot_key').map((c) => c[1]);
      expect(calls[0]).toMatchObject({ variant: 'plain', phase: 'keyup' });
      expect(calls[1]).toMatchObject({ variant: 'alt', phase: 'keydown' });
    });

    it('does not count a held key as repeated attempts', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0 });
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F12', code: 'F12' }));
      for (let i = 0; i < 5; i += 1) window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F12', code: 'F12', repeat: true }));
      expect(report.mock.calls.filter((c) => c[0] === 'devtools_key')).toHaveLength(1);
    });

    it('catches macOS and Windows screenshot chords', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0 });
      window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Digit4', key: '$', metaKey: true, shiftKey: true }));
      window.dispatchEvent(new KeyboardEvent('keydown', { code: 'KeyS', key: 's', metaKey: true, shiftKey: true }));
      expect(report.mock.calls.filter((c) => c[0] === 'screenshot_key')).toHaveLength(2);
    });
  });

  describe('configured actions', () => {
    it('does not even report an ignored signal', () => {
      const report = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0, actions: { context_menu: 'ignore', copy_attempt: 'log' } });
      document.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
      document.dispatchEvent(new Event('copy'));
      expect(report.mock.calls.map((c) => c[0])).toEqual(['copy_attempt']);
    });

    it('asks the page to blur or end, after reporting, only for those actions', () => {
      const report = vi.fn();
      const onAction = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0, onAction, actions: { print_attempt: 'blur', save_attempt: 'end', copy_attempt: 'notify', context_menu: 'log' } });
      window.dispatchEvent(new Event('beforeprint'));
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', code: 'KeyS', ctrlKey: true }));
      document.dispatchEvent(new Event('copy'));
      document.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
      expect(report).toHaveBeenCalledTimes(4);
      expect(onAction.mock.calls).toEqual([['blur', 'print_attempt'], ['end', 'save_attempt']]);
    });

    it('before the server has said anything it only logs, and skips routine focus changes', () => {
      const report = vi.fn();
      const onAction = vi.fn();
      stop = startViewerEvents(report, { minIntervalMs: 0, onAction });
      window.dispatchEvent(new Event('blur'));
      window.dispatchEvent(new Event('beforeprint'));
      expect(report.mock.calls.map((c) => c[0])).toEqual(['print_attempt']);
      expect(onAction).not.toHaveBeenCalled();
    });
  });
});

describe('developer tools open', () => {
  let stop;
  afterEach(() => { stop?.(); vi.useRealTimers(); });

  it('is reported once when the window becomes much smaller than its frame, and again after it closes and reopens', () => {
    vi.useFakeTimers();
    const report = vi.fn();
    const sizes = { outer: 1200, inner: 1200 };
    vi.spyOn(window, 'outerWidth', 'get').mockImplementation(() => sizes.outer);
    vi.spyOn(window, 'innerWidth', 'get').mockImplementation(() => sizes.inner);
    stop = startViewerEvents(report, { minIntervalMs: 0 });
    vi.advanceTimersByTime(3000);
    expect(report.mock.calls.filter((c) => c[0] === 'devtools_open')).toHaveLength(0);
    sizes.inner = 800;                                   // docked tools take 400 px
    vi.advanceTimersByTime(3000);
    expect(report.mock.calls.filter((c) => c[0] === 'devtools_open')).toEqual([['devtools_open', { via: 'size' }]]);
    sizes.inner = 1200;
    vi.advanceTimersByTime(3000);
    sizes.inner = 700;
    vi.advanceTimersByTime(3000);
    expect(report.mock.calls.filter((c) => c[0] === 'devtools_open')).toHaveLength(2);
  });

  it('can be set to be ignored like any other signal', () => {
    vi.useFakeTimers();
    const report = vi.fn();
    vi.spyOn(window, 'outerWidth', 'get').mockReturnValue(1200);
    vi.spyOn(window, 'innerWidth', 'get').mockReturnValue(500);
    stop = startViewerEvents(report, { minIntervalMs: 0, actions: { devtools_open: 'ignore' } });
    vi.advanceTimersByTime(3000);
    expect(report.mock.calls.filter((c) => c[0] === 'devtools_open')).toHaveLength(0);
  });
});
