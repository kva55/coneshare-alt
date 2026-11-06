import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { WebhookManager } from '../../../components/automations/WebhookManager';
import * as api from '../../../services/api';
import '../../../i18n';

vi.mock('../../../services/api', () => ({
  getWebhooks: vi.fn(),
  getWebhookEvents: vi.fn(),
  createWebhook: vi.fn(),
  updateWebhook: vi.fn(),
  deleteWebhook: vi.fn(),
  getWebhookSecret: vi.fn(),
  rotateWebhookSecret: vi.fn(),
  testWebhook: vi.fn(),
  getWebhookDeliveries: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const EVENTS = [
  { name: 'file_requested', description: 'A visitor asked for a file' },
  { name: 'file_checked_out', description: 'A check-out was granted' },
];
const HOOK = { id: 'h1', name: 'CI', url: 'https://ci.example.com/in', events: ['file_checked_out'], is_active: true };

describe('WebhookManager', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getWebhooks.mockResolvedValue({ data: { results: [HOOK] } });
    api.getWebhookEvents.mockResolvedValue({ data: { events: EVENTS } });
  });

  it('offers only the generic events (no chat integrations)', async () => {
    render(<WebhookManager />);
    await screen.findByText('CI');
    expect(screen.queryByText(/slack|discord|wechat|feishu/i)).toBeNull();
    expect(screen.getAllByText('file_requested').length).toBeGreaterThan(0);
  });

  it('creates a webhook and shows its signing secret', async () => {
    api.createWebhook.mockResolvedValue({ data: { signing_secret: 'whsec_abc' } });
    const user = userEvent.setup();
    render(<WebhookManager />);
    await user.type(await screen.findByLabelText(/^name/i), 'New');
    await user.type(screen.getByLabelText('URL'), 'https://x.example.com/h');
    const form = screen.getByRole('form', { name: /add webhook/i });
    await user.click(form.querySelectorAll('input[type=checkbox]')[0]);
    await user.click(screen.getByRole('button', { name: /create webhook/i }));
    await waitFor(() => expect(api.createWebhook).toHaveBeenCalledWith(
      { name: 'New', url: 'https://x.example.com/h', events: ['file_requested'] }));
    expect(await screen.findByTestId('created-secret')).toHaveTextContent('whsec_abc');
  });

  it('shows server-side URL errors', async () => {
    api.createWebhook.mockRejectedValue({ response: { data: { url: ['Private network addresses are not permitted.'] } } });
    const user = userEvent.setup();
    render(<WebhookManager />);
    await user.type(await screen.findByLabelText(/^name/i), 'Bad');
    await user.type(screen.getByLabelText('URL'), 'https://10.0.0.1/x');
    await user.click(screen.getByRole('button', { name: /create webhook/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Private network addresses are not permitted.');
  });

  it('reveals and rotates the signing secret', async () => {
    api.getWebhookSecret.mockResolvedValue({ data: { signing_secret: 'whsec_one' } });
    api.rotateWebhookSecret.mockResolvedValue({ data: { signing_secret: 'whsec_two' } });
    const user = userEvent.setup();
    render(<WebhookManager />);
    await user.click(await screen.findByRole('button', { name: /show signing secret/i }));
    expect(await screen.findByTestId('signing-secret')).toHaveTextContent('whsec_one');
    await user.click(screen.getByRole('button', { name: /rotate secret/i }));
    await waitFor(() => expect(screen.getByTestId('signing-secret')).toHaveTextContent('whsec_two'));
  });

  it('toggles events and active state through the API', async () => {
    api.updateWebhook.mockResolvedValue({ data: {} });
    const user = userEvent.setup();
    render(<WebhookManager />);
    await user.click(await screen.findByLabelText('Active CI'));
    await waitFor(() => expect(api.updateWebhook).toHaveBeenCalledWith('h1', { is_active: false }));
  });

  it('sends a test and lists deliveries', async () => {
    api.testWebhook.mockResolvedValue({ data: {} });
    api.getWebhookDeliveries.mockResolvedValue({ data: { results: [
      { id: 'd1', created_at: '2026-01-01T10:00:00Z', event_type: 'test', status: 'success', response_code: 200 }] } });
    const user = userEvent.setup();
    render(<WebhookManager />);
    await user.click(await screen.findByRole('button', { name: /send test/i }));
    expect(api.testWebhook).toHaveBeenCalledWith('h1');
    await user.click(screen.getByRole('button', { name: /deliveries/i }));
    expect(await screen.findByTestId('deliveries')).toHaveTextContent('success (200)');
  });
});
