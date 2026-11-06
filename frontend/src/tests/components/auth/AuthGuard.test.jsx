import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter, Routes, Route, useLocation } from 'react-router-dom';
import AuthGuard from '../../../components/auth/AuthGuard';
import * as api from '../../../services/api';

vi.mock('../../../services/api', () => ({
  getSession: vi.fn(),
  requestCheckoutLink: vi.fn(),
}));

function LoginLocationTracker() {
  const location = useLocation();
  return (
    <div>
      <span data-testid="login-page">Login Page</span>
      <span data-testid="from-pathname">{location.state?.from?.pathname}</span>
      <span data-testid="from-search">{location.state?.from?.search}</span>
    </div>
  );
}

describe('AuthGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  const renderWithRouter = (initialEntry = '/protected') =>
    render(
      <MemoryRouter initialEntries={[initialEntry]}>
        <Routes>
          <Route path="/login" element={<LoginLocationTracker />} />
          <Route element={<AuthGuard />}>
            <Route path="/protected" element={<div>Protected Dashboard Content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );

  it('shows a loading indicator while the session is being checked', () => {
    api.getSession.mockReturnValue(new Promise(() => {}));
    renderWithRouter('/protected');

    expect(screen.getByRole('status', { name: 'Checking session' })).toBeInTheDocument();
    expect(screen.queryByText('Protected Dashboard Content')).not.toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
  });

  it('renders the child outlet when the backend confirms the session', async () => {
    api.getSession.mockResolvedValue({ data: { id: 'u1' } });
    renderWithRouter('/protected');

    expect(await screen.findByText('Protected Dashboard Content')).toBeInTheDocument();
    expect(screen.queryByTestId('login-page')).not.toBeInTheDocument();
  });

  it('redirects to /login and preserves the location when the session is rejected', async () => {
    api.getSession.mockRejectedValue({ response: { status: 401 } });
    renderWithRouter('/protected?tab=settings');

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(screen.queryByText('Protected Dashboard Content')).not.toBeInTheDocument();
    expect(screen.getByTestId('from-pathname')).toHaveTextContent('/protected');
    expect(screen.getByTestId('from-search')).toHaveTextContent('?tab=settings');
  });

  it('ignores any token-looking values in localStorage (the session is cookie-only)', async () => {
    localStorage.setItem('access_token', 'forged');
    localStorage.setItem('refresh_token', 'forged');
    api.getSession.mockRejectedValue({ response: { status: 401 } });
    renderWithRouter('/protected');

    expect(await screen.findByTestId('login-page')).toBeInTheDocument();
    expect(screen.queryByText('Protected Dashboard Content')).not.toBeInTheDocument();
  });

  it('shows the public index (sign in / file request) at the root instead of redirecting', async () => {
    api.getSession.mockRejectedValue({ response: { status: 401 } });
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/login" element={<LoginLocationTracker />} />
          <Route element={<AuthGuard />}>
            <Route path="/" element={<div>Dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(await screen.findByText('Sign in to Coneshare')).toBeInTheDocument();
    expect(screen.getByLabelText(/enter your email to see accessible documents/i)).toBeInTheDocument();
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
  });

  it('shows the dashboard at the root once signed in', async () => {
    api.getSession.mockResolvedValue({});
    render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route element={<AuthGuard />}>
            <Route path="/" element={<div>Dashboard</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    );
    expect(await screen.findByText('Dashboard')).toBeInTheDocument();
  });
});
