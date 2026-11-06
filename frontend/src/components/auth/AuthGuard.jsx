import { useEffect, useState } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { getSession } from '../../services/api';
import { LandingPage } from '../../pages/LandingPage';

// Route guard. The session lives in httpOnly cookies, so the only way to know whether
// the user is signed in is to ask the backend.
export function AuthGuard() {
  const location = useLocation();
  const [status, setStatus] = useState('checking'); // 'checking' | 'authenticated' | 'anonymous'

  useEffect(() => {
    let active = true;
    getSession()
      .then(() => active && setStatus('authenticated'))
      .catch(() => active && setStatus('anonymous'));
    return () => {
      active = false;
    };
  }, []);

  if (status === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
        <div
          role="status"
          aria-label="Checking session"
          className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent"
        />
      </div>
    );
  }

  if (status === 'anonymous') {
    // The public index: sign in, or request files. Everything else behind sign-in redirects to the login page.
    if (location.pathname === '/') return <LandingPage />;
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <Outlet />;
}

export default AuthGuard;
