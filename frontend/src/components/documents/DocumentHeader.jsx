import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { formatDate } from '../../utils/formatters';
import { Eye, Upload, Pencil } from 'lucide-react';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '../ui/DropdownMenu';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '../ui/Tooltip';
import { ChevronDownIcon } from '../icons/ChevronDownIcon';
import { PlusIcon } from '../icons/PlusIcon';

export function DocumentHeader({
  document,
  onCreateLink,
  onPreview,
  onUploadNewVersion,
  onDownload,
  onVersionHistory,
  onDelete,
  onRenameDocument,
  isProcessing,
  canManage = true,
}) {
  const { t } = useTranslation();
  const [isEditing, setIsEditing] = useState(false);
  const [editedName, setEditedName] = useState(document.name);

  useEffect(() => {
    setEditedName(document.name);
  }, [document.name]);

  const handleStartEdit = () => {
    if (!canManage || isProcessing) return;
    setEditedName(document.name);
    setIsEditing(true);
  };

  const handleSave = () => {
    setIsEditing(false);
    const trimmed = editedName.trim();
    if (!trimmed) {
      setEditedName(document.name);
      return;
    }
    if (trimmed !== document.name) {
      onRenameDocument(trimmed);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      handleSave();
    } else if (e.key === 'Escape') {
      setIsEditing(false);
      setEditedName(document.name);
    }
  };

  return (
    <TooltipProvider>
      <div className="border-b border-gray-200 pb-5 sm:flex sm:items-center sm:justify-between">
        <div className="flex-1 min-w-0 mr-4">
          {isEditing && canManage ? (
            <input
              type="text"
              className="text-2xl font-bold leading-6 text-gray-900 border-b border-gray-900 focus:outline-none bg-transparent w-full focus:border-b-2 py-0"
              value={editedName}
              onChange={(e) => setEditedName(e.target.value)}
              onBlur={handleSave}
              onKeyDown={handleKeyDown}
              autoFocus
            />
          ) : (
            <div className="flex items-center gap-2 group max-w-full">
              <h1 
                className={`text-2xl font-bold leading-6 text-gray-900 truncate rounded px-1 -mx-1 ${canManage ? 'cursor-pointer hover:bg-gray-100/50' : ''}`}
                onClick={canManage ? handleStartEdit : undefined}
                title={canManage ? "Click to rename" : undefined}
              >
                {document.name}
              </h1>
              {canManage && (
                <button
                  onClick={handleStartEdit}
                  disabled={isProcessing}
                  className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-gray-400 hover:text-gray-600 focus:opacity-100 disabled:pointer-events-none"
                  title="Rename Document"
                  aria-label="Rename Document"
                >
                  <Pencil className="h-4 w-4" />
                </button>
              )}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {!canManage && (
              <Badge variant="outline" className="border-indigo-500/30 bg-indigo-50 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300">
                {document.created_by_user ? `Owner: ${document.created_by_user.name || document.created_by_user.email}` : t('datarooms.collaboratorRole')}
              </Badge>
            )}
            {document.updated_at && (
              <span className="text-xs text-gray-500 mr-1">
                {t('documents.lastUpdated', { date: formatDate(document.updated_at, 'PP p') })}
              </span>
            )}
            {document.uploader_info && (
              <Badge variant="secondary">
                {t('documents.uploadedBy', { name: document.uploader_info.name, email: document.uploader_info.email })}
              </Badge>
            )}
          </div>
        </div>
        <div className="mt-3 flex sm:ml-4 sm:mt-0">
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button
                  variant="outline"
                  size="icon"
                  className="mr-2"
                  onClick={onPreview}
                  disabled={document.download_only || isProcessing}
                  style={document.download_only ? { pointerEvents: 'none' } : {}}
                >
                  <Eye className="h-5 w-5" />
                  <span className="sr-only">{t('viewer.preview')}</span>
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {document.download_only ? (
                <p>{t('documents.previewNotAvailable')}</p>
              ) : (
                <p>{t('viewer.preview')}</p>
              )}
            </TooltipContent>
          </Tooltip>

          {canManage && (
            <DropdownMenu>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span>
                    <DropdownMenuTrigger asChild disabled={isProcessing}>
                      <Button
                        variant="outline"
                        size="icon"
                        className="mr-2"
                        disabled={isProcessing}
                      >
                        <Upload className="h-5 w-5" />
                        <span className="sr-only">{t('documents.uploadNewVersion')}</span>
                      </Button>
                    </DropdownMenuTrigger>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{t('documents.uploadNewVersion')}</p>
                </TooltipContent>
              </Tooltip>
              <DropdownMenuContent>
                <DropdownMenuItem onSelect={onUploadNewVersion}>
                  {t('documents.uploadFromComputer')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          {canManage && (
            <Button className="mr-2" onClick={onCreateLink} disabled={isProcessing}>
              <PlusIcon className="-ml-1 mr-2 h-5 w-5" />
              {t('documents.getLink')}
            </Button>
          )}

          <DropdownMenu>
            <DropdownMenuTrigger asChild disabled={isProcessing}>
              <Button variant="outline" size="icon" disabled={isProcessing}>
                <ChevronDownIcon className="h-5 w-5" />
                <span className="sr-only">{t('common.actions')}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={onDownload}>{t('documents.download')}</DropdownMenuItem>
              <DropdownMenuItem onSelect={onVersionHistory}>{t('documents.versions')}</DropdownMenuItem>
              {canManage && (
                <DropdownMenuItem
                  onSelect={onDelete}
                  className="text-red-600 hover:!text-red-600 hover:!bg-red-50 focus:!text-red-600 focus:!bg-red-50"
                >
                  {t('common.delete')}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </TooltipProvider>
  );
}
