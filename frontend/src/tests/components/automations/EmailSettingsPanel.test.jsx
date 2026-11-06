import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EmailSettingsPanel } from '../../../components/automations/EmailSettingsPanel';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({
  getEmailSettings: vi.fn(),
  saveEmailSettings: vi.fn(),
  sendTestEmail: vi.fn(),
  testEmailReceive: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const BASE = { enabled: false, host: '', port: 587, username: '', has_password: false, use_tls: true, use_ssl: false,
  from_email: '', source: 'environment', environment_configured: true };

describe('EmailSettingsPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getEmailSettings.mockResolvedValue({ data: BASE });
  });

  it('shows where the current configuration comes from', async () => {
    render(<EmailSettingsPanel />);
    expect(await screen.findByTestId('email-source')).toHaveTextContent(/server configuration/i);
  });

  it('saves the form, sending the password but never showing it back', async () => {
    api.saveEmailSettings.mockResolvedValue({ data: { ...BASE, enabled: true, host: 'smtp.example.com', has_password: true, source: 'database' } });
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    await user.type(await screen.findByLabelText(/smtp host/i), 'smtp.example.com');
    await user.type(screen.getByLabelText(/^password/i), 'hunter2');
    await user.type(screen.getByLabelText(/sender address/i), 'noreply@example.com');
    await user.click(screen.getByLabelText(/use these settings/i));
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveEmailSettings).toHaveBeenCalledWith(
      expect.objectContaining({ host: 'smtp.example.com', password: 'hunter2', enabled: true, port: 587 })));
    expect(screen.getByLabelText(/^password/i)).toHaveValue('');
    expect(screen.getByTestId('email-source')).toHaveTextContent(/settings below/i);
  });

  it('shows validation errors from the server', async () => {
    api.saveEmailSettings.mockRejectedValue({ response: { data: { host: ['A host is required when e-mail settings are enabled.'] } } });
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    await user.click(await screen.findByRole('button', { name: /^save$/i }));
    expect(await screen.findByRole('status')).toHaveTextContent('A host is required');
  });

  it('keeps STARTTLS and SSL mutually exclusive', async () => {
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    const tls = await screen.findByLabelText('STARTTLS');
    const ssl = screen.getByLabelText('SSL/TLS');
    expect(tls).toBeChecked();
    await user.click(ssl);
    expect(ssl).toBeChecked();
    expect(tls).not.toBeChecked();
  });

  it('sends a test message and reports the result', async () => {
    api.sendTestEmail.mockResolvedValue({ data: { detail: 'Test message sent to a@b.co.' } });
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    await user.click(await screen.findByRole('button', { name: /send test/i }));
    expect(await screen.findByRole('status')).toHaveTextContent('Test message sent to a@b.co.');
  });

  it('has an explicit opt-in for unencrypted connections', async () => {
    api.saveEmailSettings.mockResolvedValue({ data: { ...BASE } });
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    await user.click(await screen.findByLabelText(/allow an unencrypted connection/i));
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveEmailSettings).toHaveBeenCalledWith(expect.objectContaining({ allow_insecure: true })));
  });

  it('saves the inbox settings and never shows the inbox password back', async () => {
    api.saveEmailSettings.mockResolvedValue({ data: { ...BASE, imap_host: 'imap.example.com', has_imap_password: true } });
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    await user.type(await screen.findByLabelText('IMAP host'), 'imap.example.com');
    await user.type(screen.getByLabelText('Inbox password'), 'inbox-secret');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveEmailSettings).toHaveBeenCalledWith(expect.objectContaining({
      imap_host: 'imap.example.com', imap_password: 'inbox-secret', imap_port: 993 })));
    expect(screen.getByLabelText('Inbox password')).toHaveValue('');
  });

  it('tests sending and receiving once an inbox is configured', async () => {
    api.getEmailSettings.mockResolvedValue({ data: { ...BASE, imap_host: 'imap.example.com' } });
    api.testEmailReceive.mockResolvedValue({ data: { detail: 'Sent and received in 1.2s. The test message was deleted.' } });
    const user = userEvent.setup();
    render(<EmailSettingsPanel />);
    const button = await screen.findByRole('button', { name: /test sending and receiving/i });
    await waitFor(() => expect(button).toBeEnabled());
    await user.click(button);
    expect(await screen.findByRole('status')).toHaveTextContent('Sent and received in 1.2s');
  });

  it('does not offer the receive test without an inbox', async () => {
    render(<EmailSettingsPanel />);
    expect(await screen.findByRole('button', { name: /test sending and receiving/i })).toBeDisabled();
  });
});
