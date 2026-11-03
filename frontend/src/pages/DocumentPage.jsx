import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useUser } from '../contexts/UserProvider';
import { toast } from 'sonner';
import { useBreadcrumb } from '../components/layout/BreadcrumbProvider';
import { Loader2, AlertTriangle, Shield } from 'lucide-react';
import { renameDocument, getDocumentDetails, getDocumentStatus, getDocumentViews, getDocumentStats, deleteShareLink, getDocumentDownloadUrl, deleteDocument, getDataroom, getDataroomFolderContents } from '../services/api';
import { getLocalizedErrorMessage } from '../utils/errorTranslator';
import { DocumentHeader } from '../components/documents/DocumentHeader';
import { LinksTable } from '../components/documents/LinksTable';
import { ViewSessionsTable } from '../components/documents/ViewSessionsTable';
import { Stats } from '../components/documents/Stats';
import { Skeleton } from '../components/ui/Skeleton';
import { LinkSheet } from '../components/links/LinkSheet';
import { DocumentPreviewModal } from '../components/documents/DocumentPreviewModal';
import { ConfirmationDialog } from '../components/dialogs/ConfirmationDialog';
import { OwnerQnAManager } from '../components/qna/OwnerQnAManager';

export function DocumentPage() {
  const { t } = useTranslation();
  const { documentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { setBreadcrumbData } = useBreadcrumb();
  const userContext = useUser();
  const user = userContext?.user;
  const refreshUser = userContext?.refreshUser;
  const navigate = useNavigate();
  const [document, setDocument] = useState(null);
  const [stats, setStats] = useState(null);
  const [viewsData, setViewsData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [viewsLoading, setViewsLoading] = useState(true);
  const [isLinkSheetOpen, setIsLinkSheetOpen] = useState(false);
  const [editingLink, setEditingLink] = useState(null);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
  const [linkToDelete, setLinkToDelete] = useState(null);
  const [isDeleteDocDialogOpen, setIsDeleteDocDialogOpen] = useState(false);
  useEffect(() => {
    const action = searchParams.get('action');
    if (action === 'share') {
      setIsLinkSheetOpen(true);
      // Clean up the URL so the modal doesn't re-open on refresh
      const newSearchParams = new URLSearchParams(searchParams);
      newSearchParams.delete('action');
      setSearchParams(newSearchParams, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const fetchDocumentAndStats = useCallback(async (options = {}) => {
    const fromDataroomId = searchParams.get('from_dataroom');
    const fromFolderId = searchParams.get('from_folder');
    const { showSkeleton = true } = options;
    try {
      if (showSkeleton) {
        setLoading(true);
      }

      let dataroomContextData = null;
      if (fromDataroomId) {
        try {
          const contextPromises = [getDataroom(fromDataroomId)];
          if (fromFolderId) {
            contextPromises.push(getDataroomFolderContents(fromFolderId));
          }
          const [drResponse, drFolderResponseResult] = await Promise.all(contextPromises);
          dataroomContextData = {
            dataroom: drResponse.data,
            folder: drFolderResponseResult ? drFolderResponseResult.data : null,
          };
        } catch (contextError) {
          console.error("Failed to fetch dataroom context:", contextError);
          // Continue without context if it fails
        }
      }      

      const [docResponse, statsResponse] = await Promise.all([
        getDocumentDetails(documentId),
        getDocumentStats(documentId),
      ]);
      setDocument(docResponse.data);
      setStats(statsResponse.data);
      setBreadcrumbData({
        folder: docResponse.data.folder,
        documentName: docResponse.data.name,
        dataroomContext: dataroomContextData,
      });
    } catch (err) {
      // API errors are handled by the global interceptor in api.js
      console.error(err);
    } finally {
      if (showSkeleton) {
        setLoading(false);
      }
    }
  }, [documentId, searchParams, setBreadcrumbData]);

  const isOwner = document?.created_by === user?.id || document?.created_by_user?.id === user?.id;
  const isOrgAdmin = user?.role === 'admin';
  const canManage = Boolean(document && (isOwner || isOrgAdmin));

  const fetchViews = useCallback(async () => {
    if (!canManage) return;
    try {
      setViewsLoading(true);
      const response = await getDocumentViews(documentId, currentPage);
      setViewsData(response.data);
    } catch (err) {
      console.error(err);
    } finally {
      setViewsLoading(false);
    }
  }, [documentId, currentPage, canManage]);

  useEffect(() => {
    fetchDocumentAndStats();
    return () => {
      setBreadcrumbData(null);
    };
  }, [fetchDocumentAndStats, setBreadcrumbData]);

  useEffect(() => {
    if (canManage) {
      fetchViews();
    }
  }, [canManage, fetchViews]);

  useEffect(() => {
    if (!document) return;
    const isProcessingStatus = document.status === 'processing' || document.status === 'uploading';
    if (!isProcessingStatus) return;

    let isCancelled = false;
    let timerId = null;

    const poll = async () => {
      try {
        const response = await getDocumentStatus(documentId);
        if (isCancelled) return;

        const statusData = response.data;
        setDocument(prev => {
          if (!prev) return null;
          return {
            ...prev,
            status: statusData.status,
            status_message: statusData.status_message
          };
        });

        const stillProcessing = statusData.status === 'processing' || statusData.status === 'uploading';
        if (stillProcessing) {
          timerId = setTimeout(poll, 3000);
        } else {
          // When processing finally finishes, do a full reload of document details and stats
          fetchDocumentAndStats({ showSkeleton: false });
        }
      } catch (err) {
        console.error("Polling document status failed:", err);
        if (!isCancelled) {
          timerId = setTimeout(poll, 3000);
        }
      }
    };

    timerId = setTimeout(poll, 3000);

    return () => {
      isCancelled = true;
      clearTimeout(timerId);
    };
  }, [document?.status, documentId, fetchDocumentAndStats]);

  const handleCreateLink = () => {
    setEditingLink(null);
    setIsLinkSheetOpen(true);
  };

  const handleEditLink = (link) => {
    setEditingLink(link);
    setIsLinkSheetOpen(true);
  };

  const handleDeleteLink = (link) => {
    setLinkToDelete(link);
    setIsDeleteDialogOpen(true);
  };

  const handleLinkUpdate = useCallback((updatedLink) => {
    if (updatedLink) {
      // Granular update for status toggle
      setDocument(prevDoc => {
        if (!prevDoc) return null;
        const newLinks = prevDoc.share_links.map(link =>
          link.id === updatedLink.id ? updatedLink : link
        );
        return { ...prevDoc, share_links: newLinks };
      });
    } else {
      // Full refresh for create/edit from LinkSheet
      fetchDocumentAndStats({ showSkeleton: false });
      fetchViews();
    }
  }, [fetchDocumentAndStats, fetchViews]);  

  const handleConfirmDelete = async () => {
    if (!linkToDelete) return;

    try {
      await deleteShareLink(linkToDelete.id);
      toast.success(t('links.deleteSuccess', { name: linkToDelete.name || t('links.untitledLink') }));
      setIsDeleteDialogOpen(false);
      setLinkToDelete(null);
      // Refresh data
      fetchDocumentAndStats({ showSkeleton: false });
      fetchViews();
    } catch (error) {
      // Error toast is handled by the API interceptor
    }
  };

  const handlePreview = () => {
    setIsPreviewOpen(true);
  };

  const handleDownload = async () => {
    const toastId = toast.loading('Preparing download...');
    try {
      const response = await getDocumentDownloadUrl(documentId);
      window.open(response.data.download_url, '_blank');
      toast.success('Download started.', { id: toastId });
    } catch (error) {
      // The API interceptor will show a more specific error message.
      toast.dismiss(toastId);
    }
  };

  const handleRenameDocument = async (newName) => {
    try {
      await renameDocument(documentId, newName);
      setDocument(prev => ({ ...prev, name: newName }));
      setBreadcrumbData(prev => prev ? { ...prev, documentName: newName } : null);
      toast.success(t('documents.documentRenamedSuccess'));
    } catch (error) {
      console.error("Failed to rename document:", error);
      toast.error(getLocalizedErrorMessage(error, "Failed to rename document."));
    }
  };

  const handleDelete = () => {
    setIsDeleteDocDialogOpen(true);
  };

  const handleConfirmDeleteDoc = async () => {
    if (!document) return;
    try {
      await deleteDocument(documentId);
      toast.success(`Document "${document.name}" deleted.`);
      setIsDeleteDocDialogOpen(false);
      refreshUser(); // Refresh user quota
      navigate('/documents');
    } catch (error) {
      // The API interceptor will show a more specific error message.
    }
  };

  if (loading) {
    return (
      <div className="space-y-6 p-6">
        <Skeleton className="h-8 w-1/4" />
        <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
          <div className="lg:col-span-2 space-y-8">
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
          <div>
            <Skeleton className="h-24 w-full" />
          </div>
        </div>
      </div>
    );
  }

  if (!document) {
    return (
      <div className="flex h-full items-center justify-center">
        <p>Document not found.</p>
      </div>
    );
  }

  const isProcessing = document.status === 'processing' || document.status === 'uploading';
  const isError = document.status === 'error';

  return (
    <div className="container mx-auto p-4 sm:p-6">
      {(isProcessing || isError) && (
        <div className={`mb-6 rounded-md p-4 ${isError ? 'bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400' : 'bg-blue-50 text-blue-700 dark:bg-blue-900/20 dark:text-blue-400'}`}>
          <div className="flex">
            <div className="flex-shrink-0">
              {isProcessing ? (
                <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              ) : (
                <AlertTriangle className="h-5 w-5" aria-hidden="true" />
              )}
            </div>
            <div className="ml-3">
              <p className="text-sm font-medium">
                {document.status_message || (isProcessing ? 'Your document is being processed. This may take a few moments.' : 'An error occurred while processing this document.')}
              </p>
            </div>
          </div>
        </div>
      )}
      <DocumentHeader
        document={document}
        onCreateLink={handleCreateLink}
        onPreview={handlePreview}
        onDownload={handleDownload}
        onDelete={handleDelete}
        onRenameDocument={handleRenameDocument}
        isProcessing={isProcessing}
        canManage={canManage}
      />
      <div className="mt-8 space-y-8">
        <Stats stats={stats} />
        {canManage ? (
          <>
            <LinksTable
              links={document.share_links}
              onEditLink={handleEditLink}
              onDeleteLink={handleDeleteLink}
              onLinkUpdate={handleLinkUpdate}
              contextType="document"
            />
            <ViewSessionsTable
              views={viewsData?.results || []}
              totalCount={viewsData?.count || 0}
              loading={viewsLoading}
              currentPage={currentPage}
              onPageChange={setCurrentPage}
              pageSize={10}
            />
            <OwnerQnAManager documentId={documentId} shareLinks={document.share_links || []} />
          </>
        ) : (
          <div className="rounded-xl border border-blue-200 bg-blue-50/50 p-6 dark:border-blue-900/50 dark:bg-blue-950/20">
            <div className="flex items-start gap-4">
              <div className="rounded-full bg-blue-100 p-2.5 text-blue-600 dark:bg-blue-900/50 dark:text-blue-400">
                <Shield className="h-5 w-5" />
              </div>
              <div className="space-y-1">
                <h3 className="text-sm font-semibold text-blue-900 dark:text-blue-200">
                  {t('documents.collaboratorAccessNoticeTitle', { defaultValue: 'Dataroom Shared Document' })}
                </h3>
                <p className="text-sm text-blue-700 dark:text-blue-300">
                  {t('documents.collaboratorAccessNoticeDesc', {
                    defaultValue: 'You have view-only collaborator access to this document via a shared Dataroom. Direct share links, visitor tracking logs, and link security settings are managed exclusively by the document owner or organization administrators.',
                  })}
                </p>
              </div>
            </div>
          </div>
        )}
      </div>
      <LinkSheet
        isOpen={isLinkSheetOpen}
        onOpenChange={setIsLinkSheetOpen}
        document={document}
        currentLink={editingLink}
        onSuccess={handleLinkUpdate}
      />
      <DocumentPreviewModal
        isOpen={isPreviewOpen}
        onOpenChange={setIsPreviewOpen}
        documentId={documentId}
        versionId={null}
      />
      <ConfirmationDialog
        isOpen={isDeleteDialogOpen}
        onOpenChange={setIsDeleteDialogOpen}
        onConfirm={handleConfirmDelete}
        title={t('links.deleteLinkTitle')}
        description={t('links.deleteLinkConfirm', {
          name: linkToDelete?.name || t('links.untitledLink'),
        })}
        confirmText={t('common.delete')}
      />
      <ConfirmationDialog
        isOpen={isDeleteDocDialogOpen}
        onOpenChange={setIsDeleteDocDialogOpen}
        onConfirm={handleConfirmDeleteDoc}
        title="Move Document to Trash"
        description={`Are you sure you want to move "${document?.name}" to trash? You can restore it anytime from Trash.`}
        confirmText="Move to Trash"
      />
    </div>
  );
}
