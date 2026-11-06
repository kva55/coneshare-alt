import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  createShareLink,
  updateShareLink,
} from '../../services/api';
import { AccessPolicyDialog } from './AccessPolicyDialog';
import { ShareLinkAccessLog } from './ShareLinkAccessLog';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '../ui/Sheet';
import { Switch } from '../ui/Switch';

export function LinkSheet({
  isOpen,
  onOpenChange,
  document,
  dataroom,
  currentLink,
  onSuccess,
}) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [receiveEmailNotification, setReceiveEmailNotification] = useState(false);
  const [allowDownload, setAllowDownload] = useState(true);
  const [enableQna, setEnableQna] = useState(true);
  const [expiresAt, setExpiresAt] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [accessOpen, setAccessOpen] = useState(false);

  const isEditing = !!currentLink;
  const isQnaAvailable = dataroom ? dataroom.enable_qna !== false : true;
    
  useEffect(() => {
    if (isOpen) {
      if (isEditing) {
        setName(currentLink.name || '');
        setAllowDownload(currentLink.allow_download);
        setEnableQna(currentLink.enable_qna !== false);
        if (currentLink.expires_at) {
          const d = new Date(currentLink.expires_at);
          const year = d.getFullYear();
          const month = String(d.getMonth() + 1).padStart(2, '0');
          const day = String(d.getDate()).padStart(2, '0');
          const hours = String(d.getHours()).padStart(2, '0');
          const minutes = String(d.getMinutes()).padStart(2, '0');
          setExpiresAt(`${year}-${month}-${day}T${hours}:${minutes}`);
        } else {
          setExpiresAt('');
        }
        setReceiveEmailNotification(currentLink.receive_email_notification || false);
      } else {
        setName('');
        setAllowDownload(true);
        setEnableQna(true);
        setExpiresAt('');
        setReceiveEmailNotification(false);
      }
    }

    if (document?.download_only) {
      setAllowDownload(true);
    }
  }, [currentLink, document, dataroom, isEditing, isOpen]);
    
  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSaving(true);
    
    // Only what belongs to the link. Access rules live on the file (see Access settings).
    const linkData = {
      name,
      receive_email_notification: receiveEmailNotification,
      allow_download: allowDownload,
      expires_at: expiresAt ? new Date(expiresAt).toISOString() : null,
    };

    if (isQnaAvailable) {
      linkData.enable_qna = enableQna;
    }
    
    if (!isEditing) {
      if (dataroom) {
        linkData.dataroom = dataroom.id;
      } else {
        linkData.document = document.id;
      }
    }
    
    try {
      if (isEditing) {
        await updateShareLink(currentLink.id, linkData);
        toast.success(t('linkSheet.linkUpdated'));
      } else {
        await createShareLink(linkData);
        toast.success(t('linkSheet.linkCreated'));
      }

      onSuccess(); // Trigger data refresh
      onOpenChange(false); // Close the sheet
    } catch (error) {
      // Error toast is handled by the global interceptor,
      // but you could add more specific handling here if needed.
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent className="sm:max-w-3xl flex flex-col">
        <SheetHeader>
          <SheetTitle>{isEditing ? t('linkSheet.editTitle') : t('linkSheet.createTitle')}</SheetTitle>
          <SheetDescription>
            {t('linkSheet.description')}
          </SheetDescription>
        </SheetHeader>
        <form id="link-sheet-form" onSubmit={handleSubmit} className="flex-1 overflow-y-auto">
          <div className="space-y-6 py-6 pr-6">
          <div className="space-y-2">
            <Label htmlFor="name">{t('linkSheet.nameLabel')}</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('linkSheet.namePlaceholder')}
            />
          </div>

          <div className="space-y-2 rounded-md border bg-muted/30 p-4" data-testid="access-card">
            <h3 className="text-sm font-medium">{t('linkSheet.accessTitle', { defaultValue: 'Who can open it, and how' })}</h3>
            <p className="text-sm text-muted-foreground">
              {t('linkSheet.accessNote', { defaultValue: 'Allowed people, e-mail, password, NDA, watermark and the viewing protections are set on the file itself, not on the link, so they apply to every link and to check-out, even before a link exists.' })}
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => setAccessOpen(true)}>
              {t('linkSheet.editAccess', { defaultValue: 'Edit access settings' })}
            </Button>
          </div>

          {isEditing && <ShareLinkAccessLog linkId={currentLink.id} />}

          <div className="flex items-center justify-between">
            <Label htmlFor="email-notification" className="flex flex-col space-y-1">
              <span>{t('linkSheet.emailNotification')}</span>
              <span className="text-sm font-normal text-muted-foreground">
                {t('linkSheet.emailNotificationSubtitle')}
              </span>
            </Label>
            <Switch
              id="email-notification"
              checked={receiveEmailNotification}
              onCheckedChange={setReceiveEmailNotification}
            />
          </div>

          <div className="flex items-center justify-between">
            <Label htmlFor="allow-download" className="flex flex-col space-y-1">
              <span>{t('linkSheet.allowDownload')}</span>
              {document?.download_only && (
                <span className="text-sm font-normal text-muted-foreground">
                  {t('linkSheet.downloadDisabledNote')}
                </span>
              )}
            </Label>
            <Switch
              id="allow-download"
              checked={allowDownload}
              onCheckedChange={setAllowDownload}
              disabled={document?.download_only}
            />
          </div>

          <div className="flex items-center justify-between">
            <Label htmlFor="enable-qna" className="flex flex-col space-y-1">
              <span>{t('linkSheet.enableQna')}</span>
              <span className="text-sm font-normal text-muted-foreground">
                {isQnaAvailable
                  ? t('linkSheet.qnaSubtitle')
                  : t('linkSheet.qnaDisabledForDataroom')}
              </span>
            </Label>
            <Switch
              id="enable-qna"
              checked={isQnaAvailable && enableQna}
              onCheckedChange={setEnableQna}
              disabled={!isQnaAvailable}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="expires-at">{t('linkSheet.expirationDate')}</Label>
            <Input
              id="expires-at"
              type="datetime-local"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              className="w-full"
            />
            <p className="text-sm text-muted-foreground">
              {t('linkSheet.expirationSubtitle')}
            </p>
          </div>
          </div>
        </form>
        <SheetFooter>
          <Button type="submit" form="link-sheet-form" disabled={isSaving}>
            {isSaving ? t('common.saving') : t('common.save')}
          </Button>
        </SheetFooter>
      </SheetContent>
      <AccessPolicyDialog
        open={accessOpen}
        onOpenChange={setAccessOpen}
        kind={dataroom ? 'dataroom' : 'document'}
        ident={dataroom ? dataroom.id : document?.id}
        label={dataroom ? dataroom.name : document?.name}
      />
    </Sheet>
  );
}
