import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { CheckOutCenterPage } from '../../pages/CheckOutCenterPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../components/admin/AdminNav', () => ({ AdminNav: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../../services/api', () => ({
  getCheckoutPolicies: vi.fn(), createCheckoutPolicy: vi.fn(), updateCheckoutPolicy: vi.fn(), deleteCheckoutPolicy: vi.fn(),
  getCheckoutPolicyFiles: vi.fn(), setCheckoutPolicyFiles: vi.fn(), getCheckoutGuests: vi.fn(), addCheckoutGuests: vi.fn(),
  revokeCheckoutGuest: vi.fn(), getCheckoutApprovals: vi.fn(), decideCheckoutApproval: vi.fn(), getCheckoutEvents: vi.fn(),
  getCheckoutSessions: vi.fn(),
}));

const POLICY = { id: 'p1', dataroom: 'd1', dataroom_name: 'Legal', enabled: true, checkout_minutes: 5, max_attempts: 3,
  allow_download: false, downloads_per_checkout: 1, require_nda: false, nda_text: '', notify_mode: 'log', notify_email: '', updated_at: 'x' };

describe('CheckOutCenterPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getCheckoutPolicies.mockResolvedValue({ data: { results: [POLICY], datastores: [{ id: 'd1', name: 'Legal' }, { id: 'd2', name: 'HR' }], limits: { max_minutes: 5, max_attempts: 10 } } });
    api.getCheckoutPolicyFiles.mockResolvedValue({ data: { results: [
      { id: 'a', name: 'One.pdf', visible: true, configured: true, requires_approval: false, requires_nda: null, allow_download: null },
      { id: 'b', name: 'Two.pdf', visible: false, configured: false, requires_approval: false, requires_nda: null, allow_download: null },
    ] } });
    api.getCheckoutGuests.mockResolvedValue({ data: { results: [{ id: 'g1', email: 'guest@lab.local', is_active: true }, { id: 'g2', email: 'old@lab.local', is_active: false }] } });
    api.getCheckoutApprovals.mockResolvedValue({ data: { pending: [], recent: [] } });
    api.getCheckoutEvents.mockResolvedValue({ data: { results: [] } });
  });

  it('adds guests by e-mail address and revokes them', async () => {
    api.addCheckoutGuests.mockResolvedValue({ data: { added: [{}], invalid: [] } });
    api.revokeCheckoutGuest.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    expect(await screen.findByText('guest@lab.local')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^revoke$/i })).toHaveLength(1);   // revoked ones have no button
    await user.type(screen.getByLabelText(/guests \(can sign in/i), 'new@lab.local');
    await user.click(screen.getByRole('button', { name: /add guests/i }));
    await waitFor(() => expect(api.addCheckoutGuests).toHaveBeenCalledWith('p1', ['new@lab.local']));
    await user.click(screen.getByRole('button', { name: /^revoke$/i }));
    await waitFor(() => expect(api.revokeCheckoutGuest).toHaveBeenCalledWith('p1', 'g1'));
  });

  it('makes a disabled datastore obvious and enables it at once, without Save', async () => {
    api.getCheckoutPolicies.mockResolvedValue({ data: { results: [{ ...POLICY, enabled: false }], datastores: [], limits: { max_minutes: 5, max_attempts: 10 } } });
    api.updateCheckoutPolicy.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    expect(await screen.findByText(/not enabled: guests cannot sign in/i)).toBeInTheDocument();
    expect(screen.getByTestId('policy-status')).toHaveTextContent('Disabled - guests cannot sign in');
    await user.click(screen.getByRole('button', { name: /enable now/i }));
    await waitFor(() => expect(api.updateCheckoutPolicy).toHaveBeenCalledWith('p1', { enabled: true }));
    await waitFor(() => expect(screen.queryByText(/not enabled: guests cannot sign in/i)).toBeNull());
    expect(screen.getByTestId('policy-status')).toHaveTextContent(/^Enabled$/);
  });

  it('the Enabled switch also applies immediately and can switch it off again', async () => {
    api.updateCheckoutPolicy.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    expect(await screen.findByTestId('policy-status')).toHaveTextContent(/^Enabled$/);
    await user.click(screen.getByLabelText('Enabled'));
    await waitFor(() => expect(api.updateCheckoutPolicy).toHaveBeenCalledWith('p1', { enabled: false }));
    expect(await screen.findByText(/not enabled: guests cannot sign in/i)).toBeInTheDocument();
  });

  it('keeps the old state when switching fails', async () => {
    api.updateCheckoutPolicy.mockRejectedValue({ response: { data: { detail: 'nope' } } });
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    await user.click(await screen.findByLabelText('Enabled'));
    await waitFor(() => expect(api.updateCheckoutPolicy).toHaveBeenCalled());
    expect(screen.getByTestId('policy-status')).toHaveTextContent(/^Enabled$/);
  });

  it('shows why a sign-in was denied in the log', async () => {
    api.getCheckoutEvents.mockResolvedValue({ data: { results: [
      { id: 'e1', created_at: '2026-01-01T10:00:00Z', event_type: 'auth_denied', email: 'guest@lab.local', datastore: null, ip_address: '1.1.1.1', metadata: { reason: 'datastore_disabled' } }] } });
    render(<CheckOutCenterPage />);
    expect(await screen.findByText('reason=datastore_disabled')).toBeInTheDocument();
  });

  it('has no domain setting any more', async () => {
    render(<CheckOutCenterPage />);
    await screen.findByText('guest@lab.local');
    expect(screen.queryByLabelText(/allowed email domains/i)).toBeNull();
  });

  it('every file, including a fresh upload, has its own settings and no share link is involved', async () => {
    render(<CheckOutCenterPage />);
    await screen.findByText('guest@lab.local');
    expect(screen.getByText('Two.pdf')).toBeInTheDocument();
    expect(screen.getByText('new')).toBeInTheDocument();                       // never configured yet
    for (const label of ['Visible', 'Requires admin approval', 'NDA', 'Download']) {
      expect(screen.getByLabelText(`${label}: Two.pdf`)).toBeInTheDocument();
    }
    expect(screen.getByLabelText('Visible: Two.pdf')).not.toBeChecked();      // hidden until decided
    expect(screen.getByLabelText('Visible: One.pdf')).toBeChecked();
    expect(screen.getByText(/no share link is needed/i)).toBeInTheDocument();
  });

  it('saves settings, the NDA and every file setting (visibility, approval, NDA, download)', async () => {
    api.updateCheckoutPolicy.mockResolvedValue({});
    api.setCheckoutPolicyFiles.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    await screen.findByText('guest@lab.local');
    await user.click(screen.getByLabelText('Require NDA'));
    await user.type(screen.getByLabelText('NDA text'), 'Secret.');
    await user.click(screen.getByLabelText('Allow download'));
    await user.click(screen.getByLabelText('Visible: Two.pdf'));
    await user.click(screen.getByLabelText('Requires admin approval: Two.pdf'));
    await user.selectOptions(screen.getByLabelText('NDA: Two.pdf'), 'yes');
    await user.selectOptions(screen.getByLabelText('Download: One.pdf'), 'no');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.updateCheckoutPolicy).toHaveBeenCalledWith('p1', expect.objectContaining({
      require_nda: true, nda_text: 'Secret.', allow_download: true, checkout_minutes: 5 })));
    expect(api.setCheckoutPolicyFiles).toHaveBeenCalledWith('p1', [
      { id: 'a', visible: true, requires_approval: false, requires_nda: null, allow_download: false },
      { id: 'b', visible: true, requires_approval: true, requires_nda: true, allow_download: null },
    ]);
  });

  it('does not send files that were never configured, shown or touched', async () => {
    api.updateCheckoutPolicy.mockResolvedValue({});
    api.setCheckoutPolicyFiles.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    await screen.findByText('guest@lab.local');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.setCheckoutPolicyFiles).toHaveBeenCalled());
    expect(api.setCheckoutPolicyFiles.mock.calls[0][1].map((f) => f.id)).toEqual(['a']);
  });

  it('approves and denies waiting requests', async () => {
    api.getCheckoutApprovals.mockResolvedValue({ data: { pending: [
      { id: 'c1', file_name: 'Report.pdf', datastore: 'Legal', email: 'guest@lab.local', created_at: '2026-01-01T10:00:00Z' },
      { id: 'c2', file_name: 'Other.pdf', datastore: 'Legal', email: 'guest@lab.local', created_at: '2026-01-01T11:00:00Z' },
    ], recent: [] } });
    api.decideCheckoutApproval.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    const queue = await screen.findByTestId('pending-approvals');
    const rows = within(queue).getAllByRole('listitem');
    await user.click(within(rows[0]).getByRole('button', { name: /approve/i }));
    await waitFor(() => expect(api.decideCheckoutApproval).toHaveBeenCalledWith('c1', 'approve'));
    await user.click(within(rows[1]).getByRole('button', { name: /deny/i }));
    await waitFor(() => expect(api.decideCheckoutApproval).toHaveBeenCalledWith('c2', 'deny'));
  });

  it('only offers datastores that have no policy yet', async () => {
    api.createCheckoutPolicy.mockResolvedValue({});
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    const select = await screen.findByLabelText(/offer a datastore/i);
    expect([...select.options].map((o) => o.text)).toEqual(['-', 'HR']);
    await user.selectOptions(select, 'd2');
    await user.click(screen.getByRole('button', { name: /^add$/i }));
    expect(api.createCheckoutPolicy).toHaveBeenCalledWith({ dataroom: 'd2' });
  });

  it('shows the group address field only for group notifications', async () => {
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    expect(screen.queryByLabelText(/group email/i)).toBeNull();
    await user.selectOptions(await screen.findByLabelText(/on check-out/i), 'group');
    expect(screen.getByLabelText(/group email/i)).toBeInTheDocument();
  });

  it('filters the event log', async () => {
    const user = userEvent.setup();
    render(<CheckOutCenterPage />);
    await user.selectOptions(await screen.findByLabelText('Filter events'), 'requests');
    await waitFor(() => expect(api.getCheckoutEvents).toHaveBeenLastCalledWith(
      { event_type: 'file_requested,approval_required,request_approved,request_denied' }));
  });
});
