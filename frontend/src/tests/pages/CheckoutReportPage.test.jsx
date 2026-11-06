import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CheckoutReportPage } from '../../pages/CheckoutReportPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../services/api', () => ({ getCheckoutReport: vi.fn() }));

const REPORT = {
  checkout: { id: 'c1', email: 'guest@lab.local', file: 'Report.pdf', datastore: 'Legal', status: 'ended', opened_at: '2026-01-01T10:00:00Z', expires_at: '2026-01-01T10:05:00Z', ip_address: '10.0.0.1' },
  tracing_enabled: true,
  timeline: [{ at: '2026-01-01T10:00:00Z', seconds: 0, type: 'file_checked_out', detail: {} }, { at: '2026-01-01T10:01:00Z', seconds: 62, type: 'print_attempt', detail: { action: 'log' } }],
  analysis: {
    grid: { cols: 20, rows: 28 },
    movement: { horizontal: 4, vertical: 1, diagonal: 3, diagonal_share: 0.375, straight: { horizontal: 2, vertical: 0, diagonal: 1 } },
    capture_windows: [{ type: 'screenshot_key', seconds: 62, page: 1, focus_left_likely: true,
      before: { moves: 12, clicks: 1, drags: [{ kind: 'diagonal', ms: 500, length: 400 }], straight: { horizontal: 0, vertical: 0, diagonal: 1 } },
      after: { moves: 0, clicks: 0, drags: [], straight: { horizontal: 0, vertical: 0, diagonal: 0 } } }],
    basis: { cursor_samples: 120, view_samples: 30, confidence: 'low', note: 'A pointer is not an eye.' },
    pages: [{ page: 1, width: 300, height: 400, seconds_in_view: 61, seconds_cursor_over: 40, samples: 120,
      heat: Array.from({ length: 28 }, (_, r) => Array.from({ length: 20 }, (_, c) => (r === 3 && c === 4 ? 2 : 0))), heat_max: 2,
      path: [[100, 100], [300, 400]], segments: { horizontal: 4, vertical: 1, diagonal: 3 }, diagonal: [[0, 0, 50, 50]] }],
  },
};

const renderPage = () => render(
  <MemoryRouter initialEntries={['/check-out-center/sessions/c1']}>
    <Routes><Route path="/check-out-center/sessions/:checkoutId" element={<CheckoutReportPage />} /></Routes>
  </MemoryRouter>);

describe('CheckoutReportPage', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('shows who, the basis, the movement counts, the page map and the timeline', async () => {
    api.getCheckoutReport.mockResolvedValue({ data: REPORT });
    renderPage();
    expect(await screen.findByText(/guest@lab.local · Report.pdf/)).toBeInTheDocument();
    expect(screen.getByTestId('basis')).toHaveTextContent('120 cursor samples');
    expect(screen.getByTestId('basis')).toHaveTextContent('low');
    expect(screen.getByTestId('movement')).toHaveTextContent('diagonal 3');
    expect(screen.getByTestId('trace-map-1')).toBeInTheDocument();
    expect(screen.getByTestId('timeline')).toHaveTextContent('01:02');
    expect(screen.getByTestId('timeline')).toHaveTextContent('print_attempt');
    expect(screen.getByTestId('straight')).toHaveTextContent('diagonal 1');
    const win = screen.getByTestId('capture-window');
    expect(win).toHaveTextContent('screenshot_key at 01:02');
    expect(win).toHaveTextContent('window probably lost focus');
    expect(win).toHaveTextContent('press-and-drag: diagonal (500 ms)');
  });

  it('layers can be switched off', async () => {
    api.getCheckoutReport.mockResolvedValue({ data: REPORT });
    renderPage();
    const map = await screen.findByTestId('trace-map-1');
    expect(map.querySelectorAll('line').length).toBe(1);
    screen.getByLabelText(/Diagonal movements/).click();
    await waitFor(() => expect(map.querySelectorAll('line').length).toBe(0));
  });

  it('warns when tracing is off and says when the report is missing', async () => {
    api.getCheckoutReport.mockResolvedValueOnce({ data: { ...REPORT, tracing_enabled: false } });
    const { unmount } = renderPage();
    expect(await screen.findByRole('note')).toHaveTextContent(/switched off/);
    unmount();
    api.getCheckoutReport.mockRejectedValueOnce(new Error('404'));
    renderPage();
    expect(await screen.findByText(/not found/)).toBeInTheDocument();
  });
});
