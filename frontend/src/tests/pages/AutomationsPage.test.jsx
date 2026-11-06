import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AutomationsPage } from '../../pages/AutomationsPage';
import '../../i18n';

let mockUser = { role: 'admin' };
vi.mock('../../contexts/UserProvider', () => ({ useUser: () => ({ user: mockUser }) }));
vi.mock('../../components/automations/EmailSettingsPanel', () => ({ EmailSettingsPanel: () => <div>email-panel</div> }));
vi.mock('../../components/automations/WebhookManager', () => ({ WebhookManager: () => <div>webhook-manager</div> }));

describe('AutomationsPage (notifications)', () => {
  beforeEach(() => { mockUser = { role: 'admin' }; });

  it('shows email setup and webhooks to admins', () => {
    render(<AutomationsPage />);
    expect(screen.getByText('email-panel')).toBeInTheDocument();
    expect(screen.getByText('webhook-manager')).toBeInTheDocument();
  });

  it('shows members only a pointer, not the admin panels', () => {
    mockUser = { role: 'member' };
    render(<AutomationsPage />);
    expect(screen.queryByText('email-panel')).toBeNull();
    expect(screen.queryByText('webhook-manager')).toBeNull();
    expect(screen.getByText(/managed by an administrator/i)).toBeInTheDocument();
  });
});
