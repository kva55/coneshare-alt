import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AdminSecurityPage } from '../../pages/AdminSecurityPage';
import * as api from '../../services/api';
import '../../i18n';

vi.mock('../../components/admin/AdminNav', () => ({ AdminNav: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../../services/api', () => ({
  getSecuritySettings: vi.fn(), saveSecuritySettings: vi.fn(), getSecurityEvents: vi.fn(), sendSecurityTestAlert: vi.fn(),
}));

const SETTINGS = {
  code_requests_per_email: 5, code_requests_per_ip: 30, code_request_window_minutes: 15,
  failed_auth_alerts_enabled: true, failed_auth_threshold: 3, failed_auth_window_minutes: 15,
  alert_group_email: '', alert_notify_account: true,
  probe_detection_enabled: false, probe_log_authenticated: true, probe_log_unknown_areas: true,
  probe_alert_enabled: false, probe_alert_cooldown_minutes: 10, probe_disabled_rules: [],
  throttling_enabled: false, user_requests_per_minute: 600, viewer_requests_per_minute: 1500, general_requests_per_minute: 300,
  capture_actions: {}, capture_lockout_minutes: 5,
};
const CAPTURE = {
  actions: ['ignore', 'log', 'notify', 'blur', 'end'],
  events: [
    { id: 'print_attempt', label: 'Print (Ctrl+P, print dialog)', default: 'log' },
    { id: 'save_attempt', label: 'Save page (Ctrl+S)', default: 'log' },
    { id: 'window_blur', label: 'Window lost focus', default: 'ignore' },
  ],
};
const GROUPS = [
  { id: '930', name: 'Local file inclusion', description: 'Path traversal', default_enabled: true, rules: [{ id: 'path_traversal', description: 'Directory traversal', severity: 'critical' }] },
  { id: '941', name: 'Cross-site scripting', description: 'Script in input', default_enabled: true, rules: [{ id: 'xss_script_tag', description: 'Script tag', severity: 'critical' }, { id: 'xss_event_handler', description: 'Event handler', severity: 'critical' }] },
  { id: '933', name: 'PHP injection', description: 'PHP', default_enabled: false, rules: [{ id: 'php_open_tag', description: 'PHP open tag', severity: 'critical' }] },
];

describe('AdminSecurityPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getSecuritySettings.mockResolvedValue({ data: { settings: { ...SETTINGS, probe_disabled_rules: ['php_open_tag'] }, groups: GROUPS, capture: CAPTURE } });
    api.getSecurityEvents.mockResolvedValue({ data: { results: [] } });
  });

  it('saves the request limits and failed sign-in alert settings', async () => {
    api.saveSecuritySettings.mockImplementation(async (s) => ({ data: { settings: s } }));
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    const perEmail = await screen.findByLabelText('Per e-mail address');
    await user.clear(perEmail);
    await user.type(perEmail, '2');
    const threshold = screen.getByLabelText('Failed attempts');
    await user.clear(threshold);
    await user.type(threshold, '4');
    await user.type(screen.getByLabelText(/alert address/i), 'sec@lab.local');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveSecuritySettings).toHaveBeenCalledWith(expect.objectContaining({
      code_requests_per_email: 2, failed_auth_threshold: 4, alert_group_email: 'sec@lab.local' })));
  });

  it('does not crash when the experimental option is switched on, and lists the rule groups', async () => {
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    expect(screen.queryByLabelText(/e-mail the alert address when a rule trips/i)).toBeNull();
    await user.click(await screen.findByLabelText(/detect and log suspicious requests/i));
    expect(screen.getByLabelText(/e-mail the alert address when a rule trips/i)).toBeInTheDocument();
    expect(screen.getByTestId('group-930')).toBeInTheDocument();
    expect(screen.getByLabelText(/path_traversal: Directory traversal/)).toBeChecked();
    expect(screen.getByLabelText(/php_open_tag: PHP open tag/)).not.toBeChecked();   // off by default
  });

  it('switching a group off or on changes all of its rules', async () => {
    api.saveSecuritySettings.mockImplementation(async (s) => ({ data: { settings: s } }));
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    await user.click(await screen.findByLabelText(/detect and log suspicious requests/i));
    await user.click(screen.getByLabelText(/941 Cross-site scripting/));   // group checkbox: all off
    await user.click(screen.getByLabelText(/933 PHP injection/));          // group checkbox: all on
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    await waitFor(() => expect(api.saveSecuritySettings).toHaveBeenCalledWith(expect.objectContaining({
      probe_detection_enabled: true, probe_disabled_rules: ['xss_script_tag', 'xss_event_handler'] })));
  });

  it('survives an older server that sends no rule groups', async () => {
    api.getSecuritySettings.mockResolvedValue({ data: { settings: SETTINGS, rules: [] } });
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    await user.click(await screen.findByLabelText(/detect and log suspicious requests/i));
    expect(screen.getByLabelText(/e-mail the alert address/i)).toBeInTheDocument();
  });

  it('shows the server validation message', async () => {
    api.saveSecuritySettings.mockRejectedValue({ response: { data: { alert_group_email: ['Set an alert address to send probe alerts to.'] } } });
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    await user.click(await screen.findByRole('button', { name: /^save$/i }));
    expect(await screen.findByRole('status')).toHaveTextContent('Set an alert address');
  });

  it('sends a test alert and shows the outcome', async () => {
    api.sendSecurityTestAlert.mockResolvedValue({ data: { detail: 'Test alert sent to sec@lab.local.' } });
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    await user.click(await screen.findByRole('button', { name: /send test alert/i }));
    expect(await screen.findByRole('status')).toHaveTextContent('Test alert sent');
  });

  it('shows the security log and filters it', async () => {
    api.getSecurityEvents.mockResolvedValue({ data: { results: [
      { id: '1', created_at: '2026-01-01T10:00:00Z', kind: 'web_probe', rule: 'xss', method: 'GET', path: '/x', authenticated: true, ip_address: '1.2.3.4', actor: '' }] } });
    const user = userEvent.setup();
    render(<AdminSecurityPage />);
    expect(await screen.findByText('GET /x')).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText('Filter security events'), 'web_probe');
    await waitFor(() => expect(api.getSecurityEvents).toHaveBeenLastCalledWith({ kind: 'web_probe' }));
  });

  describe('request throttling', () => {
    it('is off by default, with its limits hidden until it is switched on', async () => {
      render(<AdminSecurityPage />);
      const toggle = await screen.findByLabelText('Limit the number of requests');
      expect(toggle).not.toBeChecked();
      expect(screen.queryByLabelText('Viewing, per minute')).toBeNull();
      expect(screen.getByText(/off by default/i)).toBeInTheDocument();
      expect(screen.getByText(/always apply/i)).toBeInTheDocument();
    });

    it('saves the switch and the three limits', async () => {
      api.saveSecuritySettings.mockImplementation(async (s2) => ({ data: { settings: s2 } }));
      const user = userEvent.setup();
      render(<AdminSecurityPage />);
      await user.click(await screen.findByLabelText('Limit the number of requests'));
      const viewing = screen.getByLabelText('Viewing, per minute');
      await user.clear(viewing);
      await user.type(viewing, '2500');
      await user.click(screen.getByRole('button', { name: /^save$/i }));
      await waitFor(() => expect(api.saveSecuritySettings).toHaveBeenCalledWith(expect.objectContaining({
        throttling_enabled: true, viewer_requests_per_minute: 2500, user_requests_per_minute: 600, general_requests_per_minute: 300 })));
    });
  });

  describe('print, screenshot and save attempts', () => {
    it('lists every signal with its current action, defaulting to log (and ignoring focus changes)', async () => {
      render(<AdminSecurityPage />);
      expect(await screen.findByLabelText('Print (Ctrl+P, print dialog)')).toHaveValue('log');
      expect(screen.getByLabelText('Window lost focus')).toHaveValue('ignore');
      const options = [...screen.getByLabelText('Save page (Ctrl+S)').options].map((o) => o.value);
      expect(options).toEqual(['ignore', 'log', 'notify', 'blur', 'end']);
    });

    it('saves the chosen action per signal', async () => {
      api.saveSecuritySettings.mockImplementation(async (s2) => ({ data: { settings: s2 } }));
      const user = userEvent.setup();
      render(<AdminSecurityPage />);
      await user.selectOptions(await screen.findByLabelText('Print (Ctrl+P, print dialog)'), 'blur');
      await user.selectOptions(screen.getByLabelText('Save page (Ctrl+S)'), 'end');
      await user.click(screen.getByRole('button', { name: /^save$/i }));
      await waitFor(() => expect(api.saveSecuritySettings).toHaveBeenCalledWith(expect.objectContaining({
        capture_actions: { print_attempt: 'blur', save_attempt: 'end' } })));
    });

    it('explains what a page can and cannot see', async () => {
      render(<AdminSecurityPage />);
      await screen.findByTestId('capture-actions');
      expect(screen.getByText(/operating system screenshot tools and cameras are invisible/i)).toBeInTheDocument();
    });
  });
});
