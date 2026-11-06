import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { UserProvider, useUser } from '../../contexts/UserProvider';
import * as api from '../../services/api';
import { authService } from '../../services/authService';

vi.mock('../../services/api', () => ({
  getSession: vi.fn(),
}));

vi.mock('../../services/authService', () => ({
  authService: {
    logout: vi.fn(),
  },
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

function TestConsumer() {
  const { user, refreshUser } = useUser();
  return (
    <div>
      <span data-testid="user-name">{user ? user.name : 'No User'}</span>
      <span data-testid="user-id">{user ? user.id : 'No ID'}</span>
      <button onClick={() => refreshUser()}>refresh</button>
    </div>
  );
}

describe('UserProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loads the user from the session endpoint without reading any stored token', async () => {
    api.getSession.mockResolvedValue({ data: { id: 101, name: 'Alice' } });
    const getItem = vi.spyOn(Storage.prototype, 'getItem');

    render(
      <UserProvider>
        <TestConsumer />
      </UserProvider>
    );

    await waitFor(() => {
      expect(api.getSession).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId('user-name')).toHaveTextContent('Alice');
    });
    expect(getItem).not.toHaveBeenCalledWith('access_token');
    expect(getItem).not.toHaveBeenCalledWith('refresh_token');
    getItem.mockRestore();
  });

  it('logs out and navigates to /login when the session is rejected', async () => {
    api.getSession.mockRejectedValue({ response: { status: 401 } });
    authService.logout.mockResolvedValue();

    render(
      <UserProvider>
        <TestConsumer />
      </UserProvider>
    );

    await waitFor(() => {
      expect(authService.logout).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith('/login');
    });
    expect(screen.getByTestId('user-name')).toHaveTextContent('No User');
  });

  it('keeps the user on non-auth errors', async () => {
    api.getSession.mockRejectedValue({ response: { status: 500 } });

    render(
      <UserProvider>
        <TestConsumer />
      </UserProvider>
    );

    await waitFor(() => expect(api.getSession).toHaveBeenCalled());
    expect(authService.logout).not.toHaveBeenCalled();
  });
});
