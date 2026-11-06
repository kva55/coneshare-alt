import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ShareLinkAccessLog } from '../../../components/links/ShareLinkAccessLog';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({ getShareLinkEvents: vi.fn() }));

describe('ShareLinkAccessLog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('shows events with readable labels', async () => {
    api.getShareLinkEvents.mockResolvedValue({ data: { results: [
      { id: '1', created_at: '2026-01-01T10:00:00Z', event_type: 'access_request_denied', email: 'x@y.z', ip_address: '1.2.3.4' },
    ] } });
    render(<ShareLinkAccessLog linkId="L" />);
    expect(await screen.findByText('Request denied (not on list)')).toBeInTheDocument();
    expect(screen.getByText('x@y.z')).toBeInTheDocument();
  });

  it('re-queries with the selected filter', async () => {
    api.getShareLinkEvents.mockResolvedValue({ data: { results: [] } });
    render(<ShareLinkAccessLog linkId="L" />);
    await userEvent.selectOptions(await screen.findByRole('combobox'), 'denied');
    await waitFor(() =>
      expect(api.getShareLinkEvents).toHaveBeenLastCalledWith('L', { event_type: 'access_request_denied,access_code_invalid' })
    );
  });
});
