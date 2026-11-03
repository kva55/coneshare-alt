import { lazy, Suspense } from 'react';
import { Route, Routes } from 'react-router';
import { Toaster } from 'sonner';
import './App.css';
import MainLayout from './components/layout/MainLayout';
import AuthGuard from './components/auth/AuthGuard';
import { UploadProvider } from './contexts/UploadProvider';
import { BrandingProvider } from './contexts/BrandingProvider';
import { TooltipProvider } from './components/ui/Tooltip';

// Helper for lazy loading named exports
const lazyNamed = (importer, name) =>
  lazy(() => importer().then((module) => ({ default: module[name] })));

// Public / auth routes
const LoginPage = lazy(() => import('./pages/LoginPage'));
const ShareLinkViewerPage = lazyNamed(() => import('./pages/ShareLinkViewerPage'), 'ShareLinkViewerPage');
const ErrorPage = lazyNamed(() => import('./pages/ErrorPage'), 'ErrorPage');
const NotFoundPage = lazyNamed(() => import('./pages/NotFoundPage'), 'NotFoundPage');

// Authenticated app routes
const HomePage = lazy(() => import('./pages/HomePage'));
const DocumentsPage = lazy(() => import('./pages/DocumentsPage'));
const DocumentPage = lazyNamed(() => import('./pages/DocumentPage'), 'DocumentPage');
const ShareLinkAnalyticsPage = lazyNamed(() => import('./pages/ShareLinkAnalyticsPage'), 'ShareLinkAnalyticsPage');
const TrashPage = lazy(() => import('./pages/TrashPage'));
const DataroomsPage = lazyNamed(() => import('./pages/DataroomsPage'), 'DataroomsPage');
const DataroomPage = lazyNamed(() => import('./pages/DataroomPage'), 'DataroomPage');
const AllLinksPage = lazyNamed(() => import('./pages/AllLinksPage'), 'AllLinksPage');
const AllViewSessionsPage = lazyNamed(() => import('./pages/AllViewSessionsPage'), 'AllViewSessionsPage');
const AutomationsPage = lazyNamed(() => import('./pages/AutomationsPage'), 'AutomationsPage');
const UserSettingsPage = lazy(() => import('./pages/UserSettingsPage'));
const PasswordSettingsPage = lazy(() => import('./pages/PasswordSettingsPage'));
const AdminSettingsPage = lazyNamed(() => import('./pages/AdminSettingsPage'), 'AdminSettingsPage');
const AdminBrandingPage = lazyNamed(() => import('./pages/AdminBrandingPage'), 'AdminBrandingPage');
const AdminLoginActivityPage = lazyNamed(() => import('./pages/AdminLoginActivityPage'), 'AdminLoginActivityPage');
const AdminUsersPage = lazyNamed(() => import('./pages/AdminUsersPage'), 'AdminUsersPage');
const AdminUserDetailPage = lazyNamed(() => import('./pages/AdminUserDetailPage'), 'AdminUserDetailPage');
const AdminDataroomsPage = lazyNamed(() => import('./pages/AdminDataroomsPage'), 'AdminDataroomsPage');

function RouteLoadingFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-900">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
    </div>
  );
}

function App() {
  return (
    <>
      <Toaster richColors />
      <TooltipProvider>
        <BrandingProvider>
          <UploadProvider>
            <Suspense fallback={<RouteLoadingFallback />}>
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route path="/view/:slug" element={<ShareLinkViewerPage />} />
                <Route path="/500" element={<ErrorPage />} />
                <Route element={<AuthGuard />}>
                  <Route element={<MainLayout />}>
                    <Route path="/" element={<HomePage />} />
                    <Route path="/documents" element={<DocumentsPage />} />
                    <Route path="/documents/folders/:folderId" element={<DocumentsPage />} />
                    <Route path="/documents/:documentId" element={<DocumentPage />} />
                    <Route path="/documents/:documentId/links/:linkId" element={<ShareLinkAnalyticsPage />} />
                    <Route path="/trash" element={<TrashPage />} />
                    <Route path="/datarooms" element={<DataroomsPage />} />
                    <Route path="/datarooms/:dataroomId" element={<DataroomPage />} />
                    <Route path="/analytics/links" element={<AllLinksPage />} />
                    <Route path="/analytics/view-sessions" element={<AllViewSessionsPage />} />
                    <Route path="/automations" element={<AutomationsPage />} />
                    <Route path="/settings" element={<UserSettingsPage />} />
                    <Route path="/settings/password" element={<PasswordSettingsPage />} />
                    <Route path="/admin/settings" element={<AdminSettingsPage />} />
                    <Route path="/admin/branding" element={<AdminBrandingPage />} />
                    <Route path="/admin/users" element={<AdminUsersPage />} />
                    <Route path="/admin/users/:userId" element={<AdminUserDetailPage />} />
                    <Route path="/admin/datarooms" element={<AdminDataroomsPage />} />
                    <Route path="/admin/login-activity" element={<AdminLoginActivityPage />} />
                    <Route path="*" element={<NotFoundPage />} />
                  </Route>
                </Route>
              </Routes>
            </Suspense>
          </UploadProvider>
      </BrandingProvider>
      </TooltipProvider>
    </>
  );
}

export default App;
