import { useEffect, useState, useRef, useCallback, lazy, Suspense } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import { FileDown, MessageCircle, Info, X, AlertCircle, Clock } from 'lucide-react';
import { PasswordForm } from '../components/viewer/PasswordForm';
import { useCaptureResponse, useViewerEvents } from '../hooks/useViewerEvents';
import { FocusBlurOverlay } from '../components/viewer/FocusBlurOverlay';
import { ViewingProtectionContext, useFocusLease } from '../lib/viewingProtection';
import { AccessCodeForm } from '../components/viewer/AccessCodeForm';
import { EmailForm } from '../components/viewer/EmailForm';
import { NDAForm } from '../components/viewer/NDAForm';
import { GateLayout } from '../components/viewer/GateLayout';
import { ViewerToolbar } from '../components/viewer/ViewerToolbar';
import { DocumentViewerHeader } from '../components/viewer/DocumentViewerHeader';
import { QnAPanel } from '../components/viewer/QnAPanel';
import { DataroomLoadingProgress } from '../components/viewer/DataroomLoadingProgress';
import { ViewerErrorBoundary } from '../components/viewer/ViewerErrorBoundary';

const DataroomViewer = lazy(() => import('../components/viewer/DataroomViewer').then(m => ({ default: m.DataroomViewer })));
const PreviewViewer = lazy(() => import('../components/documents/PreviewViewer').then(m => ({ default: m.PreviewViewer })));
const PreviewStatePanel = lazy(() => import('../components/documents/PreviewStatePanel').then(m => ({ default: m.PreviewStatePanel })));
import { printPdf } from '../lib/print';
import { Skeleton } from '../components/ui/Skeleton';
import {
  hasRenderablePages,
  isPreviewFailed,
  isPreviewPending,
} from '../utils/previewState';
import {
  createViewSession,
  getPublicQnaSummary,
  getShareLinkPublicMeta,
  getShareLinkViewData,
  fetchSharePageBlob,
  fetchShareTiles,
} from '../services/api';
import { Button } from '../components/ui/Button';
import { formatBytes } from '../lib/formatters';
import { useBranding } from '../contexts/BrandingProvider';
import { LanguagePicker } from '../components/common/LanguagePicker';
import { useTranslation } from 'react-i18next';
import { getLocalizedErrorMessage } from '../utils/errorTranslator';

const PREVIEW_POLL_INTERVAL_MS = 3000;

function PreviewBanner({ onClose }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);

  const handleCopy = () => {
    const cleanUrl = window.location.origin + window.location.pathname;
    if (!navigator.clipboard) {
      console.error('Clipboard API not available');
      return;
    }
    navigator.clipboard.writeText(cleanUrl)
      .then(() => {
        setCopied(true);
      })
      .catch((err) => {
        console.error('Failed to copy link:', err);
      });
  };

  return (
    <div className="fixed top-4 left-1/2 z-50 w-full max-w-xl -translate-x-1/2 px-4 animate-fadeIn">
      <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50/95 p-3 shadow-lg backdrop-blur-sm dark:border-amber-900/50 dark:bg-amber-950/90">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
        <div className="flex-1 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
          <span className="font-semibold">{t('viewer.ownerPreviewMode')}</span> {t('viewer.ownerPreviewDesc')}{' '}
          <button
            onClick={handleCopy}
            className="font-semibold underline hover:text-amber-950 dark:hover:text-white"
          >
            {copied ? t('viewer.copied') : t('viewer.copyCleanLink')}
          </button>{' '}
          {t('viewer.openIncognito')}
        </div>
        <button
          onClick={onClose}
          className="rounded-md p-0.5 text-amber-800/60 hover:bg-amber-100 hover:text-amber-800 dark:text-amber-300/60 dark:hover:bg-amber-900/50 dark:hover:text-amber-300"
          title={t('viewer.dismissBanner')}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export function ShareLinkViewerPage() {
  const { t } = useTranslation();
  const { slug } = useParams();
  const [searchParams] = useSearchParams();
  const previewToken = searchParams.get('previewToken');
  const accessToken = searchParams.get('accessToken');
  const viewSessionIdFromUrl = searchParams.get('view_session_id');
  const dataroomVisitIdFromUrl = searchParams.get('dataroom_visit_id');
  const dataroomDocumentIdFromUrl = searchParams.get('dataroom_document_id');
  const parentIdFromUrl = searchParams.get('parent_id');
  const { brandName, brandLogoUrl, brandWebsiteUrl } = useBranding();

  const [showPreviewBanner, setShowPreviewBanner] = useState(true);
  const [viewData, setViewData] = useState(null);
  const capture = useCaptureResponse();
  useViewerEvents(slug, Boolean(viewData) && !previewToken, {
    actions: viewData?.link_settings?.capture_actions,
    onAction: capture.onAction,
  });
  const blurWhenUnfocused = Boolean(viewData?.link_settings?.blur_when_unfocused);
  const focusLease = useFocusLease(slug, Boolean(viewData) && blurWhenUnfocused);
  const protection = { suspended: focusLease.suspended || capture.blurred };
  const [publicMeta, setPublicMeta] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [protectionType, setProtectionType] = useState(null);
  const [refetchTrigger, setRefetchTrigger] = useState(0);
  const [viewId, setViewId] = useState(null);
  const [dataroomVisitId, setDataroomVisitId] = useState(null);
  const [isQnaOpen, setIsQnaOpen] = useState(false);
  const [qnaThreadCount, setQnaThreadCount] = useState(0);
  const viewerRef = useRef(null);
  const hasLoadedViewDataRef = useRef(false);
  const viewIdRef = useRef(null);
  const viewDataRef = useRef(null);
  const viewerComponentRef = useRef(null);

  // Document-specific state must be declared at the top level, before any conditional returns.
  const [currentPage, setCurrentPage] = useState(1);
  const [zoomLevel, setZoomLevel] = useState(1);
  const [totalPages, setTotalPages] = useState(1);

  // Synchronize totalPages with viewData when it loads
  useEffect(() => {
    if (viewData) {
      setTotalPages(viewData.num_pages || (viewData.pages?.length || 1));
    }
  }, [viewData]);

  const handleFullScreen = () => {
    if (viewerRef.current) {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        viewerRef.current.requestFullscreen();
      }
    }
  };

  const handleZoomIn = () => setZoomLevel((prev) => Math.min(prev + 0.1, 3));
  const handleZoomOut = () => setZoomLevel((prev) => Math.max(prev - 0.1, 0.5));
  const handleFitWidth = () => setZoomLevel(1);
  const handlePageChange = (pageNumber) => {
    viewerComponentRef.current?.goToPage(pageNumber);
  };

  useEffect(() => {
    viewIdRef.current = viewId;
  }, [viewId]);

  useEffect(() => {
    viewDataRef.current = viewData;
  }, [viewData]);

  // Only reset layout & session state when switching between different share link slugs.
  // We intentionally exclude dataroomDocumentIdFromUrl here to prevent unmounting the
  // DataroomViewer and resetting the sidebar tree when navigating documents inside a dataroom.
  useEffect(() => {
    hasLoadedViewDataRef.current = false;
    setViewData(null);
    setViewId(null);
    viewIdRef.current = null;
    setCurrentPage(1);
  }, [slug]);

  useEffect(() => {
    let isCancelled = false;

    const fetchPublicMeta = async () => {
      try {
        const response = await getShareLinkPublicMeta(slug);
        if (!isCancelled) {
          setPublicMeta(response.data);
        }
      } catch {
        if (!isCancelled) {
          setPublicMeta(null);
        }
      }
    };

    fetchPublicMeta();
    return () => {
      isCancelled = true;
    };
  }, [slug]);

  useEffect(() => {
    if (viewData) {
      document.title = `${viewData.name} - ${brandName}`;
    } else if (publicMeta) {
      document.title = `${publicMeta.target_name} - ${brandName}`;
    }
  }, [viewData, publicMeta, brandName]);

  useEffect(() => {
    let isCancelled = false;
    let retryTimer = null;
    const fetchData = async () => {
      setIsLoading(!hasLoadedViewDataRef.current);
      setError(null);
      setProtectionType(null);

      if (dataroomVisitIdFromUrl) {
        setDataroomVisitId(dataroomVisitIdFromUrl);
      }

      // If we are already viewing a dataroom (either on the root scope or after deep-linking
      // into a document), skip parent-level refetching. This delegates document fetching
      // to DataroomViewer's own scoped fetcher and avoids breaking the SPA layout context.
      const isAlreadyDataroom = viewDataRef.current && (
        viewDataRef.current.link_type === 'dataroom' ||
        Boolean(viewDataRef.current.dataroom_context)
      );
      if (isAlreadyDataroom) {
        setIsLoading(false);
        return;
      }

      try {
        const response = await getShareLinkViewData(slug, {
          previewToken,
          accessToken,
          dataroomDocumentId: dataroomDocumentIdFromUrl,
          parentId: dataroomDocumentIdFromUrl ? null : parentIdFromUrl,
        });
        if (!isCancelled) {
          setViewData(response.data);
          if (viewSessionIdFromUrl) {
            // If a session ID is passed from the parent dataroom, use it.
            setViewId(viewSessionIdFromUrl);
          } else if (!viewIdRef.current && response.data?.link_settings?.id) {
            // Otherwise, create a new session for this link.
            try {
              const viewResponse = await createViewSession({ share_link: response.data.link_settings.id });
              if (!isCancelled) {
                setViewId(viewResponse.data.id);
              }
            } catch (viewError) {
              console.error('Failed to create view session:', viewError);
            }
          }
        }
      } catch (err) {
        if (!isCancelled) {
          const previousViewData = viewDataRef.current;
          const isBackgroundPreviewPoll =
            previousViewData &&
            previousViewData.link_type !== 'dataroom' &&
            isPreviewPending(previousViewData);

          if (isBackgroundPreviewPoll) {
            retryTimer = window.setTimeout(() => {
              setRefetchTrigger((current) => current + 1);
            }, PREVIEW_POLL_INTERVAL_MS);
            return;
          }

          const rawData = err.response?.data;
          const errorData =
            rawData && typeof rawData === 'object'
              ? { ...rawData }
              : {
                  message:
                    typeof rawData === 'string' && rawData.trim()
                      ? rawData.trim()
                      : 'Failed to load document. The link may be invalid or expired.',
                };

          if (err.response?.status) {
            errorData.status = err.response.status;
          }
          setError(errorData);

          if (err.response?.status === 401 && errorData?.protectionType) {
            setProtectionType(errorData.protectionType);
          }
        }
      } finally {
        if (!isCancelled) {
          hasLoadedViewDataRef.current = true;
          setIsLoading(false);
        }
      }
    };

    fetchData();
    return () => {
      isCancelled = true;
      window.clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    slug,
    previewToken,
    accessToken,
    viewSessionIdFromUrl,
    dataroomVisitIdFromUrl,
    dataroomDocumentIdFromUrl,
    // Intentionally excluded:
    // `parentIdFromUrl` changes are handled by DataroomViewer scoped fetches
    // so we avoid full-page reload/flicker during folder navigation.
    refetchTrigger,
  ]);

  useEffect(() => {
    if (!viewData || viewData.link_type === 'dataroom' || !isPreviewPending(viewData)) {
      return undefined;
    }

    const timer = window.setTimeout(() => {
      setRefetchTrigger((current) => current + 1);
    }, PREVIEW_POLL_INTERVAL_MS);

    return () => window.clearTimeout(timer);
  }, [viewData]);

  useEffect(() => {
    let isCancelled = false;
    const fetchQnaThreadCount = async () => {
      if (
        !viewId
        || !viewData
        || viewData.link_type === 'dataroom'
        || viewData.link_settings?.enable_qna === false
      ) {
        setQnaThreadCount(0);
        return;
      }

      try {
        const response = await getPublicQnaSummary(slug, {
          viewSessionId: viewId,
          dataroomDocumentId: dataroomDocumentIdFromUrl || null,
        });
        if (!isCancelled) {
          setQnaThreadCount(response.data?.thread_count || 0);
        }
      } catch (error) {
        if (!isCancelled) {
          console.error('Failed to load Q&A thread count:', error);
          setQnaThreadCount(0);
        }
      }
    };

    fetchQnaThreadCount();
    return () => {
      isCancelled = true;
    };
  }, [slug, viewId, viewData, dataroomDocumentIdFromUrl]);

  // Keyboard navigation shortcuts
  useEffect(() => {
    const handleKeyDown = (e) => {
      // Don't trigger shortcuts if user is typing in an input/textarea
      if (['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName)) {
        return;
      }

      // Allow ArrowDown / ArrowUp to scroll naturally without hijacking.
      // ArrowLeft / ArrowRight are used for slide/page navigation or video seeking.
      if (e.key === 'ArrowRight') {
        if (typeof viewerComponentRef.current?.seekBy === 'function') {
          e.preventDefault();
          viewerComponentRef.current.seekBy(5);
        } else if (typeof viewerComponentRef.current?.goToPage === 'function') {
          const nextPage = Math.min(currentPage + 1, totalPages);
          if (nextPage !== currentPage) {
            e.preventDefault();
            viewerComponentRef.current.goToPage(nextPage);
          }
        }
      } else if (e.key === 'ArrowLeft') {
        if (typeof viewerComponentRef.current?.seekBy === 'function') {
          e.preventDefault();
          viewerComponentRef.current.seekBy(-5);
        } else if (typeof viewerComponentRef.current?.goToPage === 'function') {
          const prevPage = Math.max(currentPage - 1, 1);
          if (prevPage !== currentPage) {
            e.preventDefault();
            viewerComponentRef.current.goToPage(prevPage);
          }
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [currentPage, totalPages]);

  // Pages are fetched by script (never a URL) and painted into canvases.
  const loadPage = useCallback(
    (pageNumber, options) =>
      fetchSharePageBlob(slug, pageNumber, { dataroomDocumentId: dataroomDocumentIdFromUrl, ...options }),
    [slug, dataroomDocumentIdFromUrl]
  );

  // Present only for links with the restricted viewing pane (their pages arrive blurred).
  const loadTiles = useCallback(
    (pageNumber, options) =>
      fetchShareTiles(slug, pageNumber, { dataroomDocumentId: dataroomDocumentIdFromUrl, ...options }),
    [slug, dataroomDocumentIdFromUrl]
  );

  const handlePrint = () => {
    if (!viewData || !viewData.link_settings?.allow_download) return;

    // Printing is only offered when downloads are allowed; it prints the (watermarked) download.
    if (viewData.type === 'pdf' && downloadUrl) {
      const inlineUrl = downloadUrl.includes('?') ? `${downloadUrl}&inline=true` : `${downloadUrl}?inline=true`;
      printPdf(inlineUrl);
    }
  };

  const isDataroomTarget =
    publicMeta?.target_type === 'dataroom' ||
    Boolean(dataroomVisitIdFromUrl) ||
    Boolean(dataroomDocumentIdFromUrl) ||
    Boolean(parentIdFromUrl);

  if (capture.ended) {   // the owner chose to end viewing when this attempt is detected; the server has locked it
    return (
      <div className="flex min-h-screen items-center justify-center p-8 text-center">
        <p role="alert" className="max-w-md">{t('viewer.endedByAction', { defaultValue: 'Viewing was ended because of an action that is not allowed here. Try again later.' })}</p>
      </div>
    );
  }

  if (isLoading) {
    if (isDataroomTarget) {
      return <DataroomLoadingProgress publicMeta={publicMeta} />;
    }
    return (
      <div className="h-screen w-screen bg-gray-50 p-8">
        <div className="mx-auto max-w-4xl space-y-4">
          <Skeleton className="h-12 w-1/2" />
          <Skeleton className="h-96 w-full" />
        </div>
      </div>
    );
  }

  if (protectionType === 'allowlist') {
    return (
      <AccessCodeForm
        slug={slug}
        onSuccess={() => setRefetchTrigger((c) => c + 1)}
        publicMeta={publicMeta}
      />
    );
  }

  if (protectionType === 'password') {
    return (
      <PasswordForm
        slug={slug}
        onSuccess={() => setRefetchTrigger((c) => c + 1)}
        publicMeta={publicMeta}
      />
    );
  }

  if (protectionType === 'email') {
    return (
      <EmailForm
        slug={slug}
        onSuccess={() => setRefetchTrigger((c) => c + 1)}
        publicMeta={publicMeta}
        requiresConfirmation={error?.requiresConfirmation}
        emailToConfirm={error?.emailToConfirm}
        token={accessToken}
      />
    );
  }

  if (protectionType === 'nda') {
    return (
      <NDAForm
        slug={slug}
        onSuccess={(sessionId) => {
          if (sessionId) {
            setViewId(sessionId);
          }
          setRefetchTrigger((c) => c + 1);
        }}
        publicMeta={publicMeta}
      />
    );
  }

  if (error) {
    const isExpired = error?.status === 410;

    if (isExpired) {
      return (
        <GateLayout publicMeta={publicMeta} showAccessOwnerCard={false} maxWidth="max-w-md">
          <div className="text-center py-6 space-y-3">
            <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-50 dark:bg-amber-950/40 text-amber-600 dark:text-amber-400">
              <Clock className="h-6 w-6" />
            </div>
            <h1 className="text-lg font-bold text-gray-900 dark:text-white">
              {t('viewer.linkExpiredTitle')}
            </h1>
            <p className="text-xs text-gray-500 dark:text-zinc-400 leading-relaxed max-w-xs mx-auto">
              {t('viewer.linkExpiredDescription')}
            </p>
            <p className="text-xs text-gray-400 dark:text-zinc-500">
              {t('viewer.linkExpiredHint')}
            </p>
          </div>
        </GateLayout>
      );
    }

    return (
      <GateLayout publicMeta={publicMeta} showAccessOwnerCard={false} maxWidth="max-w-md">
        <div className="text-center py-6 space-y-3">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-50 dark:bg-red-950/40 text-red-600 dark:text-red-400">
            <AlertCircle className="h-6 w-6" />
          </div>
          <h1 className="text-lg font-bold text-gray-900 dark:text-white">
            {t('viewer.linkErrorTitle')}
          </h1>
          <p className="text-xs text-gray-500 dark:text-zinc-400 leading-relaxed max-w-xs mx-auto">
            {getLocalizedErrorMessage(error)}
          </p>
        </div>
      </GateLayout>
    );
  }

  const isDataroom =
    publicMeta?.target_type === 'dataroom' ||
    viewData?.link_type === 'dataroom' ||
    Boolean(viewData?.dataroom_context);

  if (isDataroom && viewData) {
    return (
      <ViewingProtectionContext.Provider value={protection}>
      <ViewerErrorBoundary>
        <Suspense
          fallback={
            <DataroomLoadingProgress
              publicMeta={publicMeta}
              targetName={viewData?.name || publicMeta?.target_name}
            />
          }
        >
          {previewToken && showPreviewBanner && (
            <PreviewBanner onClose={() => setShowPreviewBanner(false)} />
          )}
          <DataroomViewer data={viewData} slug={slug} viewId={viewId} publicMeta={publicMeta} />
        </Suspense>
        <FocusBlurOverlay visible={(blurWhenUnfocused && !focusLease.focused) || capture.blurred} onResume={() => { capture.resume(); focusLease.recheck(); }} />
      </ViewerErrorBoundary>
      </ViewingProtectionContext.Provider>
    );
  }

  // Document-specific state and handlers
  // Only PDFs are previewable; every other type is stored but never rendered.
  const isPreviewable = Boolean(viewData && viewData.type === 'pdf');
  const canDownload = Boolean(viewData?.link_settings?.allow_download);
  const isQnaEnabled = viewData?.link_settings?.enable_qna !== false;
  const canRenderPages = hasRenderablePages(viewData);
  const showPreviewState = viewData && isPreviewable && !viewData.download_only && !canRenderPages;
  const qnaButtonLabel = (isQnaOpen
    ? t('qna.closeQna', { defaultValue: 'Close Q&A' })
    : t('qna.openQna', { defaultValue: 'Open Q&A' })) +
    (qnaThreadCount > 0 ? `, ${t('qna.threadsCount', { count: qnaThreadCount, defaultValue: `${qnaThreadCount} threads` })}` : '');

  let downloadUrl = `/api/v1/links/${slug}/download-file/`;
  if (dataroomDocumentIdFromUrl) {
    downloadUrl += `?dataroom_document_id=${dataroomDocumentIdFromUrl}`;
  }

  const effectiveLogoUrl = viewData?.resolved_brand_logo_url || viewData?.dataroom_context?.resolved_brand_logo_url || publicMeta?.resolved_brand_logo_url || brandLogoUrl;

  if (viewData && (viewData.download_only || !isPreviewable)) {
    return (
      <div className="relative flex flex-col h-screen w-screen overflow-hidden bg-gray-50">
        {previewToken && showPreviewBanner && (
          <PreviewBanner onClose={() => setShowPreviewBanner(false)} />
        )}
        <DocumentViewerHeader
          documentName={viewData.name}
          brandName={brandName}
          brandLogoUrl={effectiveLogoUrl}
          brandWebsiteUrl={brandWebsiteUrl}
          ownerName={publicMeta?.owner_name}
          ownerAvatarUrl={publicMeta?.owner_avatar_url}
          isQnaEnabled={false}
          hasViewSession={Boolean(viewId)}
        />
        <div className="flex-1 flex items-center justify-center p-4 overflow-y-auto">
          <div className="w-full max-w-xl rounded-2xl bg-white border border-gray-200/80 p-8 sm:p-10 text-center shadow-xl shadow-gray-200/50">
            <div className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-gray-100 text-gray-600 ring-8 ring-gray-100/60">
                <FileDown className="h-8 w-8" />
              </div>
            </div>
            <h1 className="text-xl sm:text-2xl font-bold tracking-tight text-gray-900 truncate" title={viewData.name}>
              {viewData.name}
            </h1>
            {viewData.file_size ? (
              <p className="mt-1 text-sm font-medium text-gray-500">{formatBytes(viewData.file_size)}</p>
            ) : null}
            <p className="mt-3 mb-6 text-sm text-gray-600 leading-relaxed max-w-sm mx-auto">
              {t('viewer.previewNotAvailableNotice')}
            </p>
            {canDownload ? (
              <Button asChild size="lg" className="w-full h-11 rounded-xl font-medium gap-2 shadow-sm">
                <a href={downloadUrl} download={viewData.name}>
                  <FileDown className="h-4 w-4" />
                  <span>{t('viewer.download')}</span>
                </a>
              </Button>
            ) : (
              <div>
                <Button size="lg" className="w-full h-11 rounded-xl font-medium gap-2" disabled>
                  <FileDown className="h-4 w-4" />
                  <span>{t('viewer.download')}</span>
                </Button>
                <p className="mt-2.5 text-xs text-gray-500">
                  {t('viewer.downloadDisabledNotice')}
                </p>
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (showPreviewState) {
    const isQnaVisible = isQnaEnabled && isPreviewFailed(viewData);

    return (
      <div className={`relative flex flex-col h-screen w-screen overflow-hidden bg-gray-50 transition-[padding] duration-200 ${isQnaOpen ? 'lg:pr-[34rem] xl:pr-[38rem]' : ''}`}>
        {previewToken && showPreviewBanner && (
          <PreviewBanner onClose={() => setShowPreviewBanner(false)} />
        )}
        <DocumentViewerHeader
          documentName={viewData.name}
          brandName={brandName}
          brandLogoUrl={brandLogoUrl}
          brandWebsiteUrl={brandWebsiteUrl}
          ownerName={publicMeta?.owner_name}
          ownerAvatarUrl={publicMeta?.owner_avatar_url}
          isQnaEnabled={isQnaVisible}
          isQnaOpen={isQnaOpen}
          onToggleQna={() => setIsQnaOpen((current) => !current)}
          qnaButtonLabel={qnaButtonLabel}
          qnaThreadCount={qnaThreadCount}
          hasViewSession={Boolean(viewId)}
        />
        <div className="flex-1 flex items-center justify-center p-4 overflow-hidden">
          <ViewerErrorBoundary>
            <Suspense
              fallback={
                <div className="flex h-full w-full items-center justify-center p-8">
                  <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
                </div>
              }
            >
              <PreviewStatePanel
                documentData={viewData}
                allowDownload={canDownload}
                downloadUrl={downloadUrl}
              />
            </Suspense>
          </ViewerErrorBoundary>
        </div>
        <QnAPanel
          open={isQnaVisible && isQnaOpen}
          onOpenChange={setIsQnaOpen}
          slug={slug}
          viewId={viewId}
          dataroomDocumentId={dataroomDocumentIdFromUrl || null}
          contextLabel={viewData.name}
          onThreadCountChange={setQnaThreadCount}
        />
      </div>
    );
  }

  return (
    <ViewingProtectionContext.Provider value={protection}>
    <div
      ref={viewerRef}
      className={`relative flex flex-col h-screen w-screen overflow-hidden transition-colors duration-300 bg-gray-50 transition-[padding] duration-200 ${isQnaOpen ? 'lg:pr-[34rem] xl:pr-[38rem]' : ''}`}
    >
      {previewToken && showPreviewBanner && (
        <PreviewBanner onClose={() => setShowPreviewBanner(false)} />
      )}
      <FocusBlurOverlay visible={(blurWhenUnfocused && !focusLease.focused) || capture.blurred} onResume={() => { capture.resume(); focusLease.recheck(); }} />
      <DocumentViewerHeader
        documentName={viewData?.name}
        brandName={brandName}
        brandLogoUrl={effectiveLogoUrl}
        brandWebsiteUrl={brandWebsiteUrl}
        ownerName={publicMeta?.owner_name}
        ownerAvatarUrl={publicMeta?.owner_avatar_url}
        isQnaEnabled={isQnaEnabled}
        isQnaOpen={isQnaOpen}
        onToggleQna={() => setIsQnaOpen((current) => !current)}
        qnaButtonLabel={qnaButtonLabel}
        qnaThreadCount={qnaThreadCount}
        hasViewSession={Boolean(viewId)}
      />
      {viewData && (
        <div className="relative flex-1 min-h-0 w-full overflow-hidden">
          <ViewerToolbar
            allowDownload={Boolean(viewData.link_settings?.allow_download)}
            downloadUrl={downloadUrl}
            downloadFileName={viewData.name}
            downloadDocumentId={dataroomDocumentIdFromUrl || null}
            onFullScreen={handleFullScreen}
            onZoomIn={handleZoomIn}
            onZoomOut={handleZoomOut}
            zoomLevel={zoomLevel}
            onFitWidth={handleFitWidth}
            onPageChange={handlePageChange}
            currentPage={currentPage}
            totalPages={totalPages}
            viewId={viewId}
            previewMode={viewData.preview_mode}
            onPrint={handlePrint}
          />
          <ViewerErrorBoundary>
            <Suspense
              fallback={
                <div className="flex h-full w-full items-center justify-center p-8">
                  <div className="h-8 w-8 animate-spin rounded-full border-2 border-indigo-600 border-t-transparent" />
                </div>
              }
            >
              <PreviewViewer
                ref={viewerComponentRef}
                documentData={viewData}
                loadPage={loadPage}
                loadTiles={viewData?.link_settings?.restricted_viewing_pane ? loadTiles : undefined}
                zoomLevel={zoomLevel}
                onPageChange={setCurrentPage}
                viewId={viewId}
                dataroomVisitId={dataroomVisitId}
              />
            </Suspense>
          </ViewerErrorBoundary>
          <QnAPanel
            open={isQnaEnabled && isQnaOpen}
            onOpenChange={setIsQnaOpen}
            slug={slug}
            viewId={viewId}
            dataroomDocumentId={dataroomDocumentIdFromUrl || null}
            contextLabel={viewData.name}
            onThreadCountChange={setQnaThreadCount}
          />
        </div>
      )}
    </div>
    </ViewingProtectionContext.Provider>
  );
}
