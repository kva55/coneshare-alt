import axios from 'axios';
import { toast } from 'sonner';
import { getLocalizedErrorMessage } from '../utils/errorTranslator';

// Authentication uses httpOnly cookies set by the backend; JavaScript never sees or
// stores a token. Cookies are sent automatically (same origin); state-changing requests
// must echo the CSRF cookie in the X-CSRFToken header.
const api = axios.create({
  baseURL: '/api/v1',
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

const SAFE_METHODS = ['get', 'head', 'options'];

export const getCsrfToken = () => {
  if (typeof document === 'undefined') return null;
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
};

// Request interceptor: language and CSRF header
api.interceptors.request.use(
  (config) => {
    config.headers['Accept-Language'] = 'en';
    const method = (config.method || 'get').toLowerCase();
    if (!SAFE_METHODS.includes(method)) {
      const csrfToken = getCsrfToken();
      if (csrfToken) {
        config.headers['X-CSRFToken'] = csrfToken;
      }
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// --- Token Refresh Logic with Race Condition Prevention ---
let isRefreshing = false;
let failedQueue = [];

const processQueue = (error) => {
  failedQueue.forEach(prom => {
    if (error) {
      prom.reject(error);
    } else {
      prom.resolve();
    }
  });
  failedQueue = [];
};

const readBlobText = (blob) => {
  if (typeof blob.text === 'function') {
    return blob.text();
  }
  if (typeof FileReader !== 'undefined') {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsText(blob);
    });
  }
  return Promise.resolve('');
};

// Response interceptor to handle 401 errors and token refresh
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    // If the error response body is a Blob (e.g. from responseType: 'blob' requests),
    // parse it so error details and protectionType can be read as JSON.
    if (
      error.response?.data instanceof Blob ||
      (error.response?.data && typeof error.response.data === 'object' && typeof error.response.data.size === 'number')
    ) {
      try {
        const text = await readBlobText(error.response.data);
        try {
          error.response.data = JSON.parse(text);
        } catch {
          if (text) {
            error.response.data = { detail: text };
          }
        }
      } catch (e) {
        // Fallback: leave as Blob
      }
    }

    const originalRequest = error.config;

    // A generated or administrator-set password must be replaced before anything else works.
    if (error.response?.status === 403 && error.response?.data?.code === 'password_change_required'
        && typeof window !== 'undefined' && window.location.pathname !== '/settings/password') {
      window.location.assign('/settings/password?forced=1');
    }

    // Visitor-side /checkout calls answer 401 themselves ("sign in"); the page handles it, and
    // there is no staff session to refresh.
    const checkoutPath = (originalRequest?.url || '').replace(/^(\/api\/v1)?\//, '');
    const isCheckoutVisitorCall = checkoutPath.startsWith('checkout/') && !checkoutPath.startsWith('checkout/admin/');

    const isPublicProtectionError =
      error.response?.status === 401 &&
      ['password', 'email', 'nda', 'allowlist'].includes(error.response?.data?.protectionType);

    const isViewDataProtectionPrompt =
      error.response?.status === 401 &&
      originalRequest?.url?.includes('/view-data/') &&
      ['password', 'email', 'nda', 'allowlist'].includes(error.response?.data?.protectionType);
          
    // Check if the error is 401, not a retry, and not from a token endpoint or public link protect prompt
    if (
      error.response?.status === 401 &&
      !originalRequest?._retry &&
      !originalRequest?.url?.includes('/token') &&
      !isPublicProtectionError &&
      !isCheckoutVisitorCall
    ) {
      if (isRefreshing) {
        // If a refresh is already in progress, queue this request
        return new Promise((resolve, reject) => {
          failedQueue.push({ resolve, reject });
        }).then(() => api(originalRequest));
      }

      originalRequest._retry = true;
      isRefreshing = true;

      const isPublicRoute =
        typeof window !== 'undefined' &&
        (window.location?.pathname?.startsWith('/view/') ||
         window.location?.pathname?.startsWith('/checkout/login'));

      const normalizedUrl = (originalRequest?.url || '').replace(/^(\/api\/v1)?\//, '');
      const isPublicEndpoint =
        normalizedUrl.startsWith('links/') ||
        normalizedUrl.startsWith('public/') ||
        normalizedUrl.startsWith('view-sessions/');

      const isPublicViewer = isPublicRoute || isPublicEndpoint;

      // The refresh token lives in an httpOnly cookie scoped to /api/v1/token/, so the
      // browser attaches it; there is nothing to read or send from JavaScript.
      return new Promise((resolve, reject) => {
        axios.post('/api/v1/token/refresh/', null, {
          withCredentials: true,
          headers: { 'X-CSRFToken': getCsrfToken() || '' },
        })
          .then(() => {
            processQueue(null);
            resolve(api(originalRequest));
          })
          .catch((err) => {
            processQueue(err);
            if (originalRequest?.skipAuthRedirect) {
              // Session probe (e.g. route guard): the caller decides what to do.
            } else if (!isPublicViewer && typeof window !== 'undefined') {
              window.location.href = '/login';
            } else {
              const errorMessage = getLocalizedErrorMessage(error);
              if (!isViewDataProtectionPrompt && !originalRequest?.skipErrorToast && errorMessage) {
                toast.error(errorMessage);
              }
            }
            reject(err);
          })
          .finally(() => {
            isRefreshing = false;
          });
      });
    }

    // For other errors, show a toast, preferring translated/mapped error messages.
    const errorMessage = getLocalizedErrorMessage(error);

    // Avoid showing an error toast for sequential gate prompts on the viewer page,
    // and for requests that report errors inline (e.g. page images).
    if (!isViewDataProtectionPrompt && !originalRequest?.skipErrorToast && errorMessage) {
      toast.error(errorMessage);
    }

    return Promise.reject(error);
  }
);

export const uploadDocument = async (file, path, onProgress) => {
  // Path contract with backend:
  // - root-relative virtual path only (no leading '/')
  // - examples: "foo.txt", "folder/sub/file.pdf"
  // Step 1: Request an upload URL from the backend
  const requestResponse = await api.post('/uploads/document/request/', {
    file_name: file.name,
    file_size: file.size,
    path: path || null,
  });

  const { upload_url, storage_key, unique_name } = requestResponse.data;

  // Step 2: Upload the file directly to the file server
  // We use axios directly here because the URL is not relative to the API base,
  // and we don't need the auth interceptors for this pre-signed URL.
  await axios.put(upload_url, file, {
    headers: {
      'Content-Type': file.type,
    },
    onUploadProgress: (progressEvent) => {
      const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
      if (onProgress) {
        onProgress(percentCompleted);
      }
    },
  });

  // Step 3: Finalize the upload with the backend
  return api.post('/uploads/document/finalize/', {
    storage_key,
    unique_name,
    content_type: file.type,
    path: path || null,
  });
};

export const getFolderContents = (id) => api.get(`/folders/${id}/`);

export const getRootFolderContents = () => api.get('/folders/');

export const getRootFolderId = () => api.get('/folders/root/');

export const createFolder = (name, parentId = null) => api.post('/folders/', { name, parent: parentId });

export const ensureFolderPaths = (paths, parentPath = null) =>
  // `paths` and `parentPath` are root-relative folder paths (no leading '/').
  api.post('/folders/ensure-paths/', { paths, parent_path: parentPath });

export const deleteDocument = (id) => api.delete(`/documents/${id}/`);

export const copyDocument = (id) => api.post(`/documents/${id}/copy/`);

export const deleteFolder = (id) => api.delete(`/folders/${id}/`);

export const renameDocument = (id, name) => api.patch(`/documents/${id}/`, { name });

export const renameFolder = (id, name) => api.patch(`/folders/${id}/`, { name });

export const updateDocument = (id, data) => api.patch(`/documents/${id}/`, data);

export const updateFolder = (id, data) => api.patch(`/folders/${id}/`, data);

export const deleteMultipleDocuments = (ids) => {
  const promises = ids.map((id) => deleteDocument(id));
  return Promise.allSettled(promises);
};

export const deleteMultipleFolders = (ids) => {
  const promises = ids.map((id) => deleteFolder(id));
  return Promise.allSettled(promises);
};

export const moveItems = ({ documentIds, folderIds, destinationFolderId }) => {
  return api.post('/actions/move/', {
    document_ids: documentIds,
    folder_ids: folderIds,
    destination_folder_id: destinationFolderId,
  });
};

export const getDocumentPreviewData = (id, versionId = null) => {
  const params = {};
  if (versionId) params.version_id = versionId;
  return api.get(`/documents/${id}/preview-data/`, { params });
};

export const rebuildDocumentPreview = (id, versionId = null) => {
  const data = {};
  if (versionId) data.version_id = versionId;
  return api.post(`/documents/${id}/rebuild-preview/`, data);
};



export const getDocumentDownloadUrl = (id) => api.get(`/documents/${id}/download/`);

export const getDocumentDetails = (id) => api.get(`/documents/${id}/`);

export const getDocumentStatus = (id) => api.get(`/documents/${id}/status/`);

export const getDocumentViews = (documentId, page = 1) =>
  api.get(`/documents/${documentId}/view-sessions/?page=${page}`);

export const getDocumentStats = (documentId) => api.get(`/documents/${documentId}/stats/`);

export const createShareLink = (data) => api.post('/share-links/', data);

export const updateShareLink = (id, data) => api.patch(`/share-links/${id}/`, data);

export const deleteShareLink = (id) => api.delete(`/share-links/${id}/`);

export const getShareLinkDetails = (id) => api.get(`/share-links/${id}/`);

export const getShareLinkViewSessions = (linkId, page = 1) =>
  api.get(`/share-links/${linkId}/view-sessions/?page=${page}`);

export const generateShareLinkPreview = (id) => api.post(`/share-links/${id}/preview/`);

export const getShareLinkViewData = (
  slug,
  {
    previewToken = null,
    accessToken = null,
    dataroomDocumentId = null,
    parentId = null,
    limit = null,
    offset = null,
    viewSessionId = null,
  } = {}
) => {
  const params = {};
  if (previewToken) params.previewToken = previewToken;
  if (accessToken) params.accessToken = accessToken;
  if (dataroomDocumentId) params.dataroom_document_id = dataroomDocumentId;
  if (parentId) params.parent_id = parentId;
  if (limit !== null && limit !== undefined) params.limit = limit;
  if (offset !== null && offset !== undefined) params.offset = offset;
  if (viewSessionId) params.view_session_id = viewSessionId;
  return api.get(`/links/${slug}/view-data/`, { params });
};

export const getShareLinkPublicMeta = (slug) =>
  api.get(`/links/${slug}/public-meta/`);

export const verifyShareLinkPassword = (slug, password) =>
  api.post(`/links/${slug}/verify-password/`, { password });

export const acceptShareLinkNda = (slug, data) =>
  api.post(`/links/${slug}/accept-nda/`, data);

export const requestShareLinkAccess = (slug, email) =>
  api.post(`/links/${slug}/request-access/`, { email });

export const confirmShareLinkEmailAccess = (slug, token) =>
  api.post(`/links/${slug}/verify-access-token/confirm/`, { token });

export const getPublicQnaThreads = (
  slug,
  {
    viewSessionId,
    dataroomDocumentId = null,
    dataroomFolderId = null,
  } = {}
) => {
  const params = {};
  if (viewSessionId) params.view_session_id = viewSessionId;
  if (dataroomDocumentId) params.dataroom_document_id = dataroomDocumentId;
  if (dataroomFolderId) params.dataroom_folder_id = dataroomFolderId;
  return api.get(`/links/${slug}/qna-threads/`, { params });
};

export const getPublicQnaSummary = (
  slug,
  {
    viewSessionId,
    dataroomDocumentId = null,
    dataroomFolderId = null,
  } = {}
) => {
  const params = {};
  if (viewSessionId) params.view_session_id = viewSessionId;
  if (dataroomDocumentId) params.dataroom_document_id = dataroomDocumentId;
  if (dataroomFolderId) params.dataroom_folder_id = dataroomFolderId;
  return api.get(`/links/${slug}/qna-summary/`, { params });
};

export const createPublicQnaThread = (
  slug,
  {
    viewSessionId,
    subject,
    body,
    dataroomDocumentId = null,
    dataroomFolderId = null,
  }
) => {
  const payload = {
    view_session_id: viewSessionId,
    subject,
    body,
  };
  if (dataroomDocumentId) payload.dataroom_document_id = dataroomDocumentId;
  if (dataroomFolderId) payload.dataroom_folder_id = dataroomFolderId;
  return api.post(`/links/${slug}/qna-threads/`, payload);
};

export const getPublicQnaMessages = (slug, threadId, { viewSessionId } = {}) => {
  const params = {};
  if (viewSessionId) params.view_session_id = viewSessionId;
  return api.get(`/links/${slug}/qna-threads/${threadId}/messages/`, { params });
};

export const createPublicQnaMessage = (slug, threadId, { viewSessionId, body }) =>
  api.post(`/links/${slug}/qna-threads/${threadId}/messages/`, {
    view_session_id: viewSessionId,
    body,
  });

export const getOwnerQnaThreads = ({
  documentId = null,
  dataroomId = null,
  shareLinkId = null,
  status = null,
} = {}) => {
  const params = {};
  if (documentId) params.document_id = documentId;
  if (dataroomId) params.dataroom_id = dataroomId;
  if (shareLinkId) params.share_link_id = shareLinkId;
  if (status && status !== 'all') params.status = status;
  return api.get('/qna-threads/', { params });
};

export const updateOwnerQnaThreadStatus = (threadId, status) =>
  api.patch(`/qna-threads/${threadId}/`, { status });

export const createOwnerQnaThread = ({
  shareLinkId,
  subject,
  body,
  dataroomDocumentId = null,
  dataroomFolderId = null,
}) => {
  const payload = {
    share_link_id: shareLinkId,
    subject,
    body,
  };
  if (dataroomDocumentId) payload.dataroom_document_id = dataroomDocumentId;
  if (dataroomFolderId) payload.dataroom_folder_id = dataroomFolderId;
  return api.post('/qna-threads/', payload);
};

export const createOwnerQnaMessage = (threadId, body) =>
  api.post(`/qna-threads/${threadId}/messages/`, { body });

export const createViewSession = (data) => api.post('/view-sessions/', data);

export const recordDataroomVisit = (viewId, { dataroomDocumentId, dataroomFolderId }) => {
  const payload = {};
  if (dataroomDocumentId) {
    payload.dataroom_document_id = dataroomDocumentId;
  } else if (dataroomFolderId) {
    payload.dataroom_folder_id = dataroomFolderId;
  }
  return api.post(`/view-sessions/${viewId}/record-visit/`, payload);
};

export const recordPageView = (data, useBeacon = false) => {
  const payload = JSON.stringify(data);
  const url = `${api.defaults.baseURL}/page-views/record/`;

  // Use sendBeacon for maximum reliability during page unload
  if (useBeacon && navigator.sendBeacon) {
    const blob = new Blob([payload], { type: 'application/json' });
    navigator.sendBeacon(url, blob);
    return Promise.resolve(); // Return a resolved promise
  }

  // Fallback to fetch with keepalive for other scenarios
  return fetch(url, {
    method: 'POST',
    body: payload,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    keepalive: true, // Critical for page unload scenarios
  });
};

export const recordLinkClick = (data, useBeacon = false) => {
  const payload = JSON.stringify(data);
  const url = `${api.defaults.baseURL}/link-clicks/record/`;

  // Use sendBeacon for maximum reliability during page unload
  if (useBeacon && navigator.sendBeacon) {
    const blob = new Blob([payload], { type: 'application/json' });
    navigator.sendBeacon(url, blob);
    return Promise.resolve();
  }

  return fetch(url, {
    method: 'POST',
    body: payload,
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    keepalive: true,
  });
};


export const getUser = (id) => api.get(`/users/${id}/`);

// Page images are delivered by script-only POST endpoints (never as a URL): the viewer
// fetches the bytes and paints them into a <canvas>. See backend core/fetch_guard.py.
const pageRequestConfig = (extra = {}) => ({
  headers: { 'X-Page-Request': '1' },
  responseType: 'blob',
  skipErrorToast: true,
  ...extra,
});

export const fetchSharePageBlob = (slug, pageNumber, { dataroomDocumentId, signal } = {}) =>
  api
    .post(
      `/links/${slug}/pages/${pageNumber}/`,
      null,
      pageRequestConfig({
        params: dataroomDocumentId ? { dataroom_document_id: dataroomDocumentId } : undefined,
        signal,
      })
    )
    .then((response) => response.data);

// Restricted viewing pane: the page itself arrives blurred (done on the server). This returns the
// clear tiles asked for, as [column, row] cells on the fixed grid (see SpotlightLayer).
const base64ToBlob = (b64, type = 'image/jpeg') => {
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  return new Blob([bytes], { type });
};

export const fetchShareTiles = (slug, pageNumber, { tiles, dataroomDocumentId, signal } = {}) =>
  api
    .post(
      `/links/${slug}/pages/${pageNumber}/tiles/`,
      { tiles },
      { ...pageRequestConfig({ params: dataroomDocumentId ? { dataroom_document_id: dataroomDocumentId } : undefined, signal }), responseType: 'json' }
    )
    .then((response) => ({
      tileSize: response.data.tile_size,
      tiles: response.data.tiles.map((t) => ({ ...t, blob: base64ToBlob(t.data) })),
    }));

// Focus lease: pages are only served to a viewer that recently said it is in focus.
export const setViewerFocus = (slug, focused) =>
  api.post(`/links/${slug}/focus/`, { focused }, { skipErrorToast: true, skipAuthRedirect: true });

export const fetchDocumentPageBlob = (documentId, pageNumber, { signal } = {}) =>
  api
    .post(`/documents/${documentId}/pages/${pageNumber}/`, null, pageRequestConfig({ signal }))
    .then((response) => response.data);

// Current session (also sets the CSRF cookie). skipAuthRedirect: the caller handles a 401.
export const getSession = () => api.get('/auth/me/', { skipAuthRedirect: true });

export const setPassword = (data) => api.post('/users/set-password/', data);

export const updateUser = (id, data) => {
  const config = {};
  if (data instanceof FormData) {
    // When sending FormData, we must let axios set the Content-Type header
    // itself so it can include the boundary.
    config.headers = {
      'Content-Type': 'multipart/form-data',
    };
  }
  return api.patch(`/users/${id}/`, data, config);
};

// Analytics
export const getDashboardSummary = () => api.get('/analytics/dashboard/');
export const getDailyVisits = () => api.get('/analytics/daily-visits/');
export const getAllActiveLinks = (page = 1) => api.get(`/analytics/links/?page=${page}`);
export const getAllViewSessions = (page = 1) => api.get(`/analytics/view-sessions/?page=${page}`);

// Notifications: generic webhooks and e-mail setup (admin)
export const getWebhookEvents = () => api.get('/webhooks/events/');
export const getWebhooks = () => api.get('/webhooks/');
export const createWebhook = (data) => api.post('/webhooks/', data);
export const updateWebhook = (id, data) => api.patch(`/webhooks/${id}/`, data);
export const deleteWebhook = (id) => api.delete(`/webhooks/${id}/`);
export const getWebhookSecret = (id) => api.get(`/webhooks/${id}/secret/`);
export const rotateWebhookSecret = (id) => api.post(`/webhooks/${id}/secret/`);
export const testWebhook = (id) => api.post(`/webhooks/${id}/test/`);
export const getWebhookDeliveries = (id, page = 1) => api.get(`/webhooks/${id}/deliveries/`, { params: { page } });
export const getEmailSettings = () => api.get('/notifications/email-settings/');
export const saveEmailSettings = (data) => api.put('/notifications/email-settings/', data);
export const sendTestEmail = () => api.post('/notifications/email-settings/test/');

export const getShareLinks = () => api.get('/share-links/');

// Datarooms
export const getDatarooms = (params) => api.get('/datarooms/', { params });
export const getDataroom = (id, params) => api.get(`/datarooms/${id}/`, { params });
export const createDataroom = (data) => api.post('/datarooms/', data);
export const createDataroomFolder = (data) => api.post('/dataroom-folders/', data);
export const renameDataroomFolder = (id, name) => api.patch(`/dataroom-folders/${id}/`, { name });
export const renameDataroomDocument = (id, name) => api.patch(`/dataroom-documents/${id}/`, { name });
export const updateDataroomFolder = (id, data) => api.patch(`/dataroom-folders/${id}/`, data);
export const updateDataroomDocument = (id, data) => api.patch(`/dataroom-documents/${id}/`, data);
export const updateDataroom = (id, data) => api.patch(`/datarooms/${id}/`, data);
export const getDataroomCollaborators = (dataroomId) => api.get(`/datarooms/${dataroomId}/collaborators/`);
export const addDataroomCollaborators = (dataroomId, data) => api.post(`/datarooms/${dataroomId}/collaborators/`, data);
export const removeDataroomCollaborator = (dataroomId, userId) => api.delete(`/datarooms/${dataroomId}/collaborators/${userId}/`);
export const transferDataroomOwnership = (dataroomId, newOwnerId) => api.post(`/datarooms/${dataroomId}/transfer-ownership/`, { new_owner_id: newOwnerId });
export const getEligibleCollaborators = (dataroomId, query = '') => api.get(`/datarooms/${dataroomId}/eligible-collaborators/`, { params: query ? { q: query } : {} });
export const updateDataroomBranding = (id, {
  name,
  bannerFile,
  removeBanner = false,
  bannerMode,
  logoMode,
  brandLogoOverrideFile,
  removeBrandLogoOverride = false,
  showFileIndex,
  enableQna,
  storageQuotaMb,
}) => {
  const formData = new FormData();
  if (name !== undefined) {
    formData.append('name', name);
  }
  if (bannerFile) {
    formData.append('branding_banner', bannerFile);
  }
  formData.append('remove_branding_banner', removeBanner ? 'true' : 'false');
  if (bannerMode !== undefined) {
    formData.append('banner_mode', bannerMode);
  }
  if (logoMode !== undefined) {
    formData.append('logo_mode', logoMode);
  }
  if (brandLogoOverrideFile) {
    formData.append('brand_logo_override', brandLogoOverrideFile);
  }
  formData.append('remove_brand_logo_override', removeBrandLogoOverride ? 'true' : 'false');
  if (showFileIndex !== undefined) {
    formData.append('show_file_index', showFileIndex ? 'true' : 'false');
  }
  if (enableQna !== undefined) {
    formData.append('enable_qna', enableQna ? 'true' : 'false');
  }
  if (storageQuotaMb !== undefined) {
    formData.append('storage_quota_mb', storageQuotaMb);
  }
  return api.patch(`/datarooms/${id}/`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};
export const deleteDataroom = (id) => api.delete(`/datarooms/${id}/`);
export const addContentToDataroom = (id, data) => api.post(`/datarooms/${id}/add-content/`, data);
export const removeContentFromDataroom = (id, data) => api.post(`/datarooms/${id}/remove-content/`, data);
export const moveDataroomContent = (id, data) => api.post(`/datarooms/${id}/move-content/`, data);
export const reorderDataroomItems = (id, data) => api.post(`/datarooms/${id}/reorder-items/`, data);
export const ensureDataroomFolderPaths = (dataroomId, paths, parentFolderId = null) =>
  api.post(`/datarooms/${dataroomId}/ensure-paths/`, { paths, parent_folder_id: parentFolderId });

export const uploadDataroomDocument = async (dataroomId, file, destinationFolderId = null, path = null, onProgress = null) => {
  const requestResponse = await api.post(`/datarooms/${dataroomId}/uploads/request/`, {
    file_name: file.name,
    file_size: file.size,
    destination_folder_id: destinationFolderId || null,
    path: path || null,
  });

  const { upload_url, storage_key, unique_name } = requestResponse.data;

  await axios.put(upload_url, file, {
    headers: {
      'Content-Type': file.type || 'application/octet-stream',
    },
    onUploadProgress: (progressEvent) => {
      if (onProgress && progressEvent.total) {
        const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(percentCompleted);
      }
    },
  });

  return api.post(`/datarooms/${dataroomId}/uploads/finalize/`, {
    storage_key,
    unique_name,
    content_type: file.type || 'application/octet-stream',
    destination_folder_id: destinationFolderId || null,
    path: path || null,
  });
};
export const getDataroomFolderContents = (folderId) => api.get(`/dataroom-folders/${folderId}/`);
export const getShareLinksForDataroom = (dataroomId) => api.get(`/share-links/?dataroom_id=${dataroomId}`);
export const updateDataroomLinkSettings = (linkId, settings) => api.patch(`/share-links/${linkId}/dataroom-settings/`, settings);
export const getDataroomViewSessions = (dataroomId, page = 1) => api.get(`/datarooms/${dataroomId}/view-sessions/?page=${page}`);
export const getDataroomStats = (dataroomId) => api.get(`/datarooms/${dataroomId}/stats/`);

export const downloadDataroomFolder = (slug, folderId, viewSessionId = null) => {
  const params = new URLSearchParams();
  if (viewSessionId) {
    params.set('view_session_id', viewSessionId);
  }
  const queryString = params.toString() ? `?${params.toString()}` : '';
  const downloadUrl = `/api/v1/links/${slug}/download-folder/${folderId}/${queryString}`;
  const link = document.createElement('a');
  link.href = downloadUrl;
  link.setAttribute('download', '');
  document.body.appendChild(link);
  link.click();
  link.remove();
};

export const downloadDataroomArchive = (slug, viewSessionId = null) => {
  const params = new URLSearchParams();
  if (viewSessionId) {
    params.set('view_session_id', viewSessionId);
  }
  const queryString = params.toString() ? `?${params.toString()}` : '';
  const downloadUrl = `/api/v1/links/${slug}/download-dataroom/${queryString}`;
  const link = document.createElement('a');
  link.href = downloadUrl;
  link.setAttribute('download', '');
  document.body.appendChild(link);
  link.click();
  link.remove();
};

// Admin
export const getAdminBranding = () => api.get('/admin/organization/');
export const updateAdminBranding = (data) => api.patch('/admin/organization/', data, {
  headers: { 'Content-Type': 'multipart/form-data' }
});
export const getAdminSettings = () => api.get('/admin/settings/');
export const updateAdminSetting = (key, value) => api.patch(`/admin/settings/${key}/`, { value });
export const getAdminUsers = (params = 1) => {
  const queryParams = typeof params === 'number' ? { page: params } : params;
  return api.get('/admin/users/', { params: queryParams });
};
export const exportAdminUsers = (params = {}) => {
  return api.get('/admin/users/export/', {
    params,
    responseType: 'blob',
  });
};
export const getAdminUserDetails = (id) => api.get(`/admin/users/${id}/`);
export const getAdminUserShareLinks = (id, page = 1) => api.get(`/admin/users/${id}/share-links/?page=${page}`);
export const getAdminUserDatarooms = (id, page = 1) => api.get(`/admin/users/${id}/datarooms/?page=${page}`);
export const createAdminUser = (data) => api.post('/admin/users/', data);
export const updateAdminUser = (id, data) => api.patch(`/admin/users/${id}/`, data);
export const deleteAdminUser = (id) => api.delete(`/admin/users/${id}/`);
export const recalculateAdminUserQuota = (id) => api.post(`/admin/users/${id}/recalculate-quota/`);
export const resetAdminUserPassword = (id, data) => api.post(`/admin/users/${id}/reset-password/`, data);
export const getAdminLoginActivities = (page = 1) => api.get(`/admin/login-activities/?page=${page}`);
export const getAdminDatarooms = (params = {}) => api.get('/admin/datarooms/', { params });
export const getAdminDataroom = (id) => api.get(`/admin/datarooms/${id}/`);
export const updateAdminDataroom = (id, data) => api.patch(`/admin/datarooms/${id}/`, data);
export const deleteAdminDataroom = (id) => api.delete(`/admin/datarooms/${id}/`);
export const transferAdminDataroomOwnership = (id, newOwnerId) =>
  api.post(`/admin/datarooms/${id}/transfer-ownership/`, { new_owner_id: newOwnerId });
export const getAdminDataroomCollaborators = (id) =>
  api.get(`/admin/datarooms/${id}/collaborators/`);
export const addAdminDataroomCollaborators = (id, data) =>
  api.post(`/admin/datarooms/${id}/collaborators/`, data);
export const removeAdminDataroomCollaborator = (id, userId) =>
  api.delete(`/admin/datarooms/${id}/collaborators/${userId}/`);
export const getAdminEligibleCollaborators = (id, query = '') =>
  api.get(`/admin/datarooms/${id}/eligible-collaborators/`, { params: query ? { q: query } : {} });
    
// Trash
export const getTrashItems = (page = 1) => api.get(`/trash/?page=${page}`);
export const restoreTrashItem = (id) => api.post(`/trash/${id}/restore/`);
export const permanentDeleteTrashItem = (id) => api.delete(`/trash/${id}/permanent/`);
export const emptyTrash = () => api.delete('/trash/empty/');

// API Keys

export default api;

// Allow-list access: the answer to a request never depends on the address (see backend/sharelinks/access.py).
export const requestShareLinkAccessCode = (slug, email) =>
  api.post(`/links/${slug}/access/request/`, { email }, { skipErrorToast: true });

export const verifyShareLinkAccessCode = (slug, email, code) =>
  api.post(`/links/${slug}/access/verify/`, { email, code }, { skipErrorToast: true });

export const reportViewerEvent = (slug, eventType, metadata = {}) =>
  api.post(`/links/${slug}/events/`, { event_type: eventType, metadata }, { skipErrorToast: true, skipAuthRedirect: true });

// Last-chance delivery when the page is going away; fire-and-forget by design.
const beacon = (path, eventType, metadata = {}) => {
  try {
    if (typeof navigator === 'undefined' || !navigator.sendBeacon) return false;
    return navigator.sendBeacon(`/api/v1${path}`, new Blob([JSON.stringify({ event_type: eventType, metadata })], { type: 'application/json' }));
  } catch {
    return false;
  }
};
export const beaconViewerEvent = (slug, eventType, metadata) => beacon(`/links/${slug}/events/`, eventType, metadata);
export const beaconCheckoutEvent = (checkoutId, eventType, metadata) => beacon(`/checkout/checkouts/${checkoutId}/events/`, eventType, metadata);
export const reportCheckoutEvent = (checkoutId, eventType, metadata = {}) =>
  api.post(`/checkout/checkouts/${checkoutId}/events/`, { event_type: eventType, metadata }, { skipErrorToast: true, skipAuthRedirect: true });

// Owner side: allow-list and access log
export const getAllowedEmails = (linkId) => api.get(`/share-links/${linkId}/allowed-emails/`);
export const addAllowedEmails = (linkId, emails) => api.post(`/share-links/${linkId}/allowed-emails/`, { emails });
export const revokeAllowedDomain = (linkId, entryId) => api.delete(`/share-links/${linkId}/allowed-domains/${entryId}/`);
export const revokeAllowedEmail = (linkId, entryId) => api.delete(`/share-links/${linkId}/allowed-emails/${entryId}/`);
export const resendAllowedEmailInvitation = (linkId, entryId) =>
  api.post(`/share-links/${linkId}/allowed-emails/${entryId}/resend/`);
export const getShareLinkEvents = (linkId, params = {}) => api.get(`/share-links/${linkId}/events/`, { params });

export const getAccessEvents = (params = {}) => api.get('/analytics/access-events/', { params });
export const getAccessEventSummary = (days = 7) => api.get('/analytics/access-events/summary/', { params: { days } });

// ---- /checkout (guest) ----
export const requestCheckoutLink = (email) =>
  api.post('/checkout/auth/request/', { email }, { skipErrorToast: true });
export const peekCheckoutLink = (token) =>
  api.get('/checkout/auth/link/', { params: { token }, skipErrorToast: true });
export const consumeCheckoutLink = (token) =>
  api.post('/checkout/auth/consume/', { token }, { skipErrorToast: true });
export const getCheckoutSession = () => api.get('/checkout/session/', { skipErrorToast: true });
export const endCheckoutSession = () => api.delete('/checkout/session/', { skipErrorToast: true });
export const getCheckoutDatarooms = () => api.get('/checkout/datarooms/', { skipErrorToast: true });
export const acceptCheckoutNda = (policyId) => api.post(`/checkout/datarooms/${policyId}/nda/`, null, { skipErrorToast: true });
export const requestCheckoutFile = (fileId) => api.post(`/checkout/files/${fileId}/request/`, null, { skipErrorToast: true });
export const openCheckout = (id) => api.post(`/checkout/checkouts/${id}/open/`, null, { skipErrorToast: true });
export const returnCheckout = (id) => api.post(`/checkout/checkouts/${id}/return/`, null, { skipErrorToast: true });
export const getCheckoutDetail = (id) => api.get(`/checkout/checkouts/${id}/`, { skipErrorToast: true });

// A page of an open check-out: fetched by script and painted into a canvas, never a URL.
export const fetchCheckoutPageBlob = (checkoutId, pageNumber, { signal } = {}) =>
  api
    .post(`/checkout/checkouts/${checkoutId}/pages/${pageNumber}/`, null, pageRequestConfig({ signal }))
    .then((response) => response.data);

export const setCheckoutFocus = (checkoutId, focused) =>
  api.post(`/checkout/checkouts/${checkoutId}/focus/`, { focused }, { skipErrorToast: true, skipAuthRedirect: true });

// Restricted viewing pane on a checked-out file: the page arrives blurred, clear tiles are fetched around the cursor.
export const fetchCheckoutTiles = (checkoutId, pageNumber, { tiles, signal } = {}) =>
  api
    .post(`/checkout/checkouts/${checkoutId}/pages/${pageNumber}/tiles/`, { tiles }, { ...pageRequestConfig({ signal }), responseType: 'json' })
    .then((response) => ({
      tileSize: response.data.tile_size,
      tiles: response.data.tiles.map((t) => ({ ...t, blob: base64ToBlob(t.data) })),
    }));

export const downloadCheckout = (checkoutId) =>
  api
    .post(`/checkout/checkouts/${checkoutId}/download/`, null, pageRequestConfig())
    .then((response) => ({
      blob: response.data,
      filename: /filename="([^"]+)"/.exec(response.headers?.['content-disposition'] || '')?.[1] || 'file',
    }));

// ---- /checkout (admin dashboard) ----
export const getCheckoutPolicies = () => api.get('/checkout/admin/policies/');
export const createCheckoutPolicy = (data) => api.post('/checkout/admin/policies/', data);
export const updateCheckoutPolicy = (id, data) => api.patch(`/checkout/admin/policies/${id}/`, data);
export const deleteCheckoutPolicy = (id) => api.delete(`/checkout/admin/policies/${id}/`);
export const getCheckoutPolicyFiles = (id) => api.get(`/checkout/admin/policies/${id}/files/`);
export const setCheckoutPolicyFiles = (id, files) => api.put(`/checkout/admin/policies/${id}/files/`, { files });
export const getCheckoutGuests = (id) => api.get(`/checkout/admin/policies/${id}/guests/`);
export const addCheckoutGuests = (id, emails) => api.post(`/checkout/admin/policies/${id}/guests/`, { emails });
export const revokeCheckoutGuest = (id, guestId) => api.delete(`/checkout/admin/policies/${id}/guests/${guestId}/`);
export const getCheckoutApprovals = () => api.get('/checkout/admin/approvals/');
export const decideCheckoutApproval = (checkoutId, decision) => api.post(`/checkout/admin/approvals/${checkoutId}/${decision}/`);
export const sendCheckoutTrace = (id, body) => api.post(`/checkout/checkouts/${id}/trace/`, body, { skipErrorToast: true });
export const getCheckoutSessions = (params = {}) => api.get('/checkout/admin/checkouts/', { params });
export const getCheckoutReport = (id) => api.get(`/checkout/admin/checkouts/${id}/report/`);
export const getCheckoutEvents = (params = {}) => api.get('/checkout/admin/events/', { params });

// ---- security settings (admin) ----
export const getLoginNotice = () => api.get('/security/login-notice/');
export const getRetentionSettings = () => api.get('/retention/settings/');
export const saveRetentionSettings = (data) => api.put('/retention/settings/', data);
export const runRetentionNow = () => api.post('/retention/run/');
export const getRetentionLog = () => api.get('/retention/log/');
export const getSecuritySettings = () => api.get('/security/settings/');
export const saveSecuritySettings = (data) => api.put('/security/settings/', data);
export const getSecurityEvents = (params = {}) => api.get('/security/events/', { params });
export const sendSecurityTestAlert = () => api.post('/security/test-alert/');
export const testEmailReceive = () => api.post('/notifications/email-settings/test-receive/');

export const getLinkViewerActivity = (linkId) => api.get(`/share-links/${linkId}/viewer-activity/`);
export const getDocumentViewerActivity = (documentId) => api.get(`/documents/${documentId}/viewer-activity/`);

// ---- interaction activity (Wireshark-style filter) ----
export const getInteractions = (params = {}) => api.get('/analytics/interactions/', { params });

// per-file check-out access (no share link involved)
export const getFileCheckoutAccess = (dataroomDocumentId) => api.get(`/checkout/dataroom-documents/${dataroomDocumentId}/access/`);
export const saveFileCheckoutAccess = (dataroomDocumentId, data) => api.put(`/checkout/dataroom-documents/${dataroomDocumentId}/access/`, data);

// Access rules of a document or datastore, independent of share links
export const getAccessPolicy = (kind, ident) => api.get(`/access-policies/${kind}/${ident}/`);
export const saveAccessPolicy = (kind, ident, data) => api.put(`/access-policies/${kind}/${ident}/`, data);
