import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CheckoutViewerPage } from '../../pages/CheckoutViewerPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../services/api', () => ({
  getCheckoutDetail: vi.fn(), returnCheckout: vi.fn(), downloadCheckout: vi.fn(), fetchCheckoutPageBlob: vi.fn(),
  reportCheckoutEvent: vi.fn(), beaconCheckoutEvent: vi.fn(), fetchCheckoutTiles: vi.fn(), setCheckoutFocus: vi.fn(),
  sendCheckoutTrace: vi.fn(() => Promise.resolve()),
}));
vi.mock('../../components/documents/PageCanvas', () => ({
  PageCanvas: ({ pageNumber, loadPage, loadTiles }) => <div data-testid={`page-${pageNumber}`} data-loader={typeof loadPage} data-tiles={typeof loadTiles} />,
}));

const detail = (over = {}) => ({ id: 'c1', name: 'Report.pdf', expires_at: new Date(Date.now() + 300000).toISOString(),
  pages: [{ page_number: 1, width: 300, height: 400 }, { page_number: 2, width: 300, height: 400 }], allow_download: false, ...over });

const renderViewer = () => render(
  <MemoryRouter initialEntries={['/checkout/view/c1']}>
    <Routes>
      <Route path="/checkout/view/:checkoutId" element={<CheckoutViewerPage />} />
      <Route path="/checkout" element={<div>portal</div>} />
    </Routes>
  </MemoryRouter>
);

describe('CheckoutViewerPage', () => {
  beforeEach(() => vi.clearAllMocks());
  afterEach(() => vi.useRealTimers());

  it('shows the pages as canvases with a countdown, and no download when not allowed', async () => {
    api.getCheckoutDetail.mockResolvedValue({ data: detail() });
    renderViewer();
    expect(await screen.findByText('Report.pdf')).toBeInTheDocument();
    expect(screen.getByTestId('countdown')).toBeInTheDocument();
    expect(await screen.findByTestId('page-1')).toHaveAttribute('data-loader', 'function');
    expect(screen.getByTestId('page-2')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^download$/i })).toBeNull();
  });

  it('offers a download only when allowed', async () => {
    api.getCheckoutDetail.mockResolvedValue({ data: detail({ allow_download: true }) });
    renderViewer();
    expect(await screen.findByRole('button', { name: /^download$/i })).toBeInTheDocument();
  });

  it('checking in ends the check-out and goes back', async () => {
    api.getCheckoutDetail.mockResolvedValue({ data: detail() });
    api.returnCheckout.mockResolvedValue({});
    const user = userEvent.setup();
    renderViewer();
    await user.click(await screen.findByRole('button', { name: /check in/i }));
    expect(api.returnCheckout).toHaveBeenCalledWith('c1');
    expect(await screen.findByText('portal')).toBeInTheDocument();
  });

  it('says so when the check-out is not open', async () => {
    api.getCheckoutDetail.mockRejectedValue({ response: { status: 403 } });
    renderViewer();
    expect(await screen.findByText(/this check-out has ended/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /back to your files/i })).toBeInTheDocument();
  });

  it('ends the view when the time runs out', async () => {
    vi.useFakeTimers();
    api.getCheckoutDetail.mockResolvedValue({ data: detail({ expires_at: new Date(Date.now() + 2000).toISOString() }) });
    renderViewer();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(screen.getByText(/this check-out has ended/i)).toBeInTheDocument();
  });

  it('says the file type is not viewable (and points to Download when allowed)', async () => {
    api.getCheckoutDetail.mockResolvedValue({ data: detail({ pages: [], preview: { state: 'unsupported' }, allow_download: true }) });
    renderViewer();
    expect(await screen.findByTestId('preview-state')).toHaveTextContent(/type cannot be viewed in the browser.*Use Download/i);
  });

  it('says so when a preview is still being prepared, and keeps checking until the pages appear', async () => {
    vi.useFakeTimers();
    api.getCheckoutDetail
      .mockResolvedValueOnce({ data: detail({ pages: [], preview: { state: 'processing' } }) })
      .mockResolvedValue({ data: detail() });
    renderViewer();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByTestId('preview-state')).toHaveTextContent(/still being prepared/i);
    await act(async () => { await vi.advanceTimersByTimeAsync(5500); });
    expect(screen.getByTestId('page-1')).toBeInTheDocument();
  });

  it('after a minute of waiting it says the background job may not be running', async () => {
    vi.useFakeTimers();
    api.getCheckoutDetail.mockResolvedValue({ data: detail({ pages: [], preview: { state: 'processing' } }) });
    renderViewer();
    await act(async () => { await Promise.resolve(); await Promise.resolve(); });
    expect(screen.getByTestId('preview-state')).not.toHaveTextContent(/taking longer/i);
    await act(async () => { await vi.advanceTimersByTimeAsync(66000); });
    expect(screen.getByTestId('preview-state')).toHaveTextContent(/taking longer than usual.*background worker/i);
  });

  it('says so when the preview failed, instead of a vague message', async () => {
    api.getCheckoutDetail.mockResolvedValue({ data: detail({ pages: [], preview: { state: 'failed' } }) });
    renderViewer();
    expect(await screen.findByTestId('preview-state')).toHaveTextContent(/could not be prepared/i);
  });

  it('records what the guest tries to do (print, save, inspect, right-click) against this check-out', async () => {
    api.getCheckoutDetail.mockResolvedValue({ data: detail() });
    api.reportCheckoutEvent.mockResolvedValue({});
    renderViewer();
    await screen.findByText('Report.pdf');
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', code: 'KeyP', ctrlKey: true }));
      await Promise.resolve();
    });
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', code: 'KeyS', ctrlKey: true }));
      window.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    const types = api.reportCheckoutEvent.mock.calls.map((c) => c[1]);
    expect(api.reportCheckoutEvent.mock.calls[0][0]).toBe('c1');
    expect(types).toEqual(expect.arrayContaining(['print_attempt', 'save_attempt', 'context_menu']));
  });

  it('records nothing once the check-out is over', async () => {
    api.getCheckoutDetail.mockRejectedValue({ response: { status: 403 } });
    renderViewer();
    await screen.findByText(/this check-out has ended/i);
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', code: 'KeyP', ctrlKey: true }));
    expect(api.reportCheckoutEvent).not.toHaveBeenCalled();
  });

  describe('zoom', () => {
    it('zooms the pages in and out, shows the level and can reset it', async () => {
      api.getCheckoutDetail.mockResolvedValue({ data: detail() });
      const user = userEvent.setup();
      renderViewer();
      const pages = await screen.findByTestId('pages');
      expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
      expect(pages).toHaveStyle({ width: '800px' });
      await user.click(screen.getByRole('button', { name: 'Zoom in' }));
      expect(screen.getByTestId('zoom-level')).toHaveTextContent('125%');
      expect(screen.getByTestId('pages')).toHaveStyle({ width: '1000px' });
      await user.click(screen.getByRole('button', { name: 'Zoom out' }));
      await user.click(screen.getByRole('button', { name: 'Zoom out' }));
      expect(screen.getByTestId('zoom-level')).toHaveTextContent('75%');
      await user.click(screen.getByRole('button', { name: 'Fit width' }));
      expect(screen.getByTestId('zoom-level')).toHaveTextContent('100%');
    });

    it('stops at the limits', async () => {
      api.getCheckoutDetail.mockResolvedValue({ data: detail() });
      const user = userEvent.setup();
      renderViewer();
      await screen.findByTestId('pages');
      for (let i = 0; i < 12; i += 1) await user.click(screen.getByRole('button', { name: 'Zoom in' }));
      expect(screen.getByTestId('zoom-level')).toHaveTextContent('300%');
      expect(screen.getByRole('button', { name: 'Zoom in' })).toBeDisabled();
      for (let i = 0; i < 12; i += 1) await user.click(screen.getByRole('button', { name: 'Zoom out' }));
      expect(screen.getByTestId('zoom-level')).toHaveTextContent('50%');
      expect(screen.getByRole('button', { name: 'Zoom out' })).toBeDisabled();
    });

    it('has no zoom when there is nothing to zoom', async () => {
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ pages: [], preview: { state: 'unsupported' } }) });
      renderViewer();
      await screen.findByTestId('preview-state');
      expect(screen.queryByRole('group', { name: 'Zoom' })).toBeNull();
    });
  });

  describe('protections set on the file', () => {
    it('restricted viewing pane: pages get the tile loader', async () => {
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ protections: { restricted_viewing_pane: true, blur_when_unfocused: false } }) });
      renderViewer();
      expect(await screen.findByTestId('page-1')).toHaveAttribute('data-tiles', 'function');
    });

    it('without it there is no tile loader', async () => {
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ protections: { restricted_viewing_pane: false, blur_when_unfocused: false } }) });
      renderViewer();
      expect(await screen.findByTestId('page-1')).toHaveAttribute('data-tiles', 'undefined');
      expect(api.setCheckoutFocus).not.toHaveBeenCalled();
    });

    it('blur when unfocused: reports focus to the server and covers the page when focus is lost', async () => {
      vi.spyOn(document, 'hasFocus').mockReturnValue(true);
      api.setCheckoutFocus.mockResolvedValue({});
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ protections: { restricted_viewing_pane: false, blur_when_unfocused: true } }) });
      renderViewer();
      await screen.findByText('Report.pdf');
      await waitFor(() => expect(api.setCheckoutFocus).toHaveBeenCalledWith('c1', true));
      expect(screen.queryByTestId('focus-blur-overlay')).toBeNull();
      document.hasFocus.mockReturnValue(false);
      await act(async () => { window.dispatchEvent(new Event('blur')); });
      expect(api.setCheckoutFocus).toHaveBeenLastCalledWith('c1', false);
      expect(await screen.findByTestId('focus-blur-overlay')).toBeInTheDocument();
    });
  });

  describe('configured responses to an attempt', () => {
    const actions = { print_attempt: 'blur', save_attempt: 'end', copy_attempt: 'log', context_menu: 'ignore' };

    it('blur covers the page until the viewer clicks to continue', async () => {
      api.reportCheckoutEvent.mockResolvedValue({});
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ capture_actions: actions }) });
      const user = userEvent.setup();
      renderViewer();
      await screen.findByText('Report.pdf');
      await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'p', code: 'KeyP', ctrlKey: true })); await Promise.resolve(); });
      const overlay = await screen.findByTestId('focus-blur-overlay');
      await user.click(overlay);
      expect(screen.queryByTestId('focus-blur-overlay')).toBeNull();
    });

    it('end replaces the viewer with a message', async () => {
      api.reportCheckoutEvent.mockResolvedValue({});
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ capture_actions: actions }) });
      renderViewer();
      await screen.findByText('Report.pdf');
      await act(async () => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', code: 'KeyS', ctrlKey: true })); await Promise.resolve(); });
      expect(await screen.findByRole('alert')).toHaveTextContent(/viewing was ended because of an action/i);
      expect(screen.queryByText('Report.pdf')).toBeNull();
    });

    it('an ignored signal does nothing at all', async () => {
      api.reportCheckoutEvent.mockResolvedValue({});
      api.getCheckoutDetail.mockResolvedValue({ data: detail({ capture_actions: actions }) });
      renderViewer();
      await screen.findByText('Report.pdf');
      await act(async () => { window.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true })); await Promise.resolve(); });
      expect(api.reportCheckoutEvent).not.toHaveBeenCalled();
      expect(screen.queryByTestId('focus-blur-overlay')).toBeNull();
    });
  });
});
