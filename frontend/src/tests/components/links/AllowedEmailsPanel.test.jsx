import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AllowedEmailsPanel, splitEmails } from '../../../components/links/AllowedEmailsPanel';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({
  getAllowedEmails: vi.fn(),
  addAllowedEmails: vi.fn(),
  revokeAllowedEmail: vi.fn(),
  revokeAllowedDomain: vi.fn(),
  resendAllowedEmailInvitation: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe('splitEmails', () => {
  it('splits on commas, spaces and newlines, lowercases and de-duplicates', () => {
    expect(splitEmails('A@x.io, b@x.io\na@x.io; ')).toEqual(['a@x.io', 'b@x.io']);
  });
});

describe('AllowedEmailsPanel', () => {
  beforeEach(() => vi.clearAllMocks());

  it('while creating, only collects text for the parent', async () => {
    const onChange = vi.fn();
    render(<AllowedEmailsPanel pendingText="" onPendingTextChange={onChange} />);
    await userEvent.type(screen.getByLabelText(/allowed emails/i), 'a');
    expect(onChange).toHaveBeenCalledWith('a');
    expect(api.getAllowedEmails).not.toHaveBeenCalled();
  });

  it('lists entries and revokes one', async () => {
    api.getAllowedEmails.mockResolvedValue({ data: { results: [
      { id: '1', email: 'a@x.io', is_active: true, last_access_at: null },
      { id: '2', email: 'old@x.io', is_active: false, last_access_at: null },
    ] } });
    api.revokeAllowedEmail.mockResolvedValue({});
    render(<AllowedEmailsPanel linkId="L" />);
    expect(await screen.findByText('a@x.io')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /revoke/i })).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: /revoke/i }));
    await waitFor(() => expect(api.revokeAllowedEmail).toHaveBeenCalledWith('L', '1'));
  });

  it('adds parsed addresses', async () => {
    api.getAllowedEmails.mockResolvedValue({ data: { results: [] } });
    api.addAllowedEmails.mockResolvedValue({ data: { added: [{}], reinstated: [], existing: [], invalid: [] } });
    render(<AllowedEmailsPanel linkId="L" />);
    await userEvent.type(await screen.findByLabelText(/allowed emails/i), 'A@x.io, b@x.io');
    await userEvent.click(screen.getByRole('button', { name: /add & invite/i }));
    await waitFor(() => expect(api.addAllowedEmails).toHaveBeenCalledWith('L', ['a@x.io', 'b@x.io']));
  });

  it('lists domain-wide rules and revokes one', async () => {
    api.getAllowedEmails.mockResolvedValue({ data: { results: [], domains: [
      { id: 'd1', domain: 'partner.com', is_active: true },
      { id: 'd2', domain: 'old.com', is_active: false },
    ] } });
    api.revokeAllowedDomain.mockResolvedValue({});
    render(<AllowedEmailsPanel linkId="L" />);
    expect(await screen.findByText(/@partner\.com/)).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /revoke/i })).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: /revoke/i }));
    await waitFor(() => expect(api.revokeAllowedDomain).toHaveBeenCalledWith('L', 'd1'));
  });

  it('says that an empty list means an open link', async () => {
    api.getAllowedEmails.mockResolvedValue({ data: { results: [], domains: [] } });
    render(<AllowedEmailsPanel linkId="L" />);
    expect(await screen.findByText(/anyone with the link can open it/i, { selector: 'p.text-sm' })).toBeInTheDocument();
  });

  it('sends domains and addresses together in one request', async () => {
    api.getAllowedEmails.mockResolvedValue({ data: { results: [], domains: [] } });
    api.addAllowedEmails.mockResolvedValue({ data: { added: [], reinstated: [], existing: [], invalid: [], domains_added: [{}] } });
    render(<AllowedEmailsPanel linkId="L" />);
    await userEvent.type(await screen.findByLabelText(/allowed emails/i), 'a@x.io, @Partner.com');
    await userEvent.click(screen.getByRole('button', { name: /add & invite/i }));
    await waitFor(() => expect(api.addAllowedEmails).toHaveBeenCalledWith('L', ['a@x.io', '@partner.com']));
  });
});
