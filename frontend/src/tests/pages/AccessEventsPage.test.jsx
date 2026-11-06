import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessEventsPage } from '../../pages/AccessEventsPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../services/api', () => ({ getAccessEvents: vi.fn() }));

describe('AccessEventsPage', () => {
  beforeEach(() => vi.clearAllMocks());

  it('lists events and filters to access requests', async () => {
    api.getAccessEvents.mockResolvedValue({ data: { count: 1, next: null, previous: null, results: [
      { id: '1', created_at: '2026-01-01T10:00:00Z', event_type: 'access_request', email: 'a@x.io', ip_address: '1.1.1.1' },
    ] } });
    render(<MemoryRouter><AccessEventsPage /></MemoryRouter>);
    expect(await screen.findByText('Access request')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole('combobox'), 'requests');
    await waitFor(() =>
      expect(api.getAccessEvents).toHaveBeenLastCalledWith({ event_type: 'access_request,access_request_throttled', page: 1 })
    );
  });
});
