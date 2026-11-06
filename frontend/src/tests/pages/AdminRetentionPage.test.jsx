import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdminRetentionPage } from '../../pages/AdminRetentionPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../components/admin/AdminNav', () => ({ AdminNav: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../../services/api', () => ({
  getRetentionSettings: vi.fn(), saveRetentionSettings: vi.fn(), runRetentionNow: vi.fn(), getRetentionLog: vi.fn(),
}));

const BASE = { mode: 'off', after_value: 1, after_unit: 'years', yearly_month: 1, yearly_day: 1, overwrite: true };

describe('AdminRetentionPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getRetentionSettings.mockResolvedValue({ data: { settings: BASE, due_now: 0, cutoff: null } });
    api.getRetentionLog.mockResolvedValue({ data: { results: [{ id: 'l1', created_at: '2026-01-01T00:00:00Z', document_name: 'Old.pdf', reason: 'file_date', overwritten: true, ok: true, error: '' }] } });
    api.saveRetentionSettings.mockImplementation(async (s) => ({ data: { settings: s, due_now: 2, cutoff: null } }));
  });

  it('is off by default, shows the log, and offers overwrite on', async () => {
    render(<AdminRetentionPage />);
    expect(await screen.findByLabelText(/Policy for files/)).toHaveValue('off');
    expect(screen.getByLabelText(/Overwrite stored data/)).toBeChecked();
    expect(screen.getByTestId('retention-log')).toHaveTextContent('Old.pdf');
    expect(screen.queryByLabelText(/Custom: after/)).toBeNull();
  });

  it('saves a one-week policy chosen from the presets', async () => {
    const user = userEvent.setup();
    render(<AdminRetentionPage />);
    await user.selectOptions(await screen.findByLabelText(/Policy for files/), 'after_upload');
    await user.click(screen.getByRole('button', { name: '1 week' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.saveRetentionSettings).toHaveBeenCalledWith(expect.objectContaining({ mode: 'after_upload', after_value: 1, after_unit: 'weeks' })));
    expect(await screen.findByTestId('due-now')).toHaveTextContent('2 file(s)');
  });

  it('offers a yearly date', async () => {
    const user = userEvent.setup();
    render(<AdminRetentionPage />);
    await user.selectOptions(await screen.findByLabelText(/Policy for files/), 'yearly');
    expect(screen.getByLabelText(/Every year on/)).toHaveValue('1');
  });
});
