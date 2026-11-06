import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { getFileCheckoutAccess, saveFileCheckoutAccess } from '../../services/api';
import { Button } from '../ui/Button';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/Dialog';
import { AccessPolicyDialog } from '../links/AccessPolicyDialog';

const TRI = { '': null, yes: true, no: false };
const triValue = (v) => (v === true ? 'yes' : v === false ? 'no' : '');
const DEFAULTS = { visible: false, requires_approval: false, requires_nda: null, allow_download: null };

/**
 * Check-out access settings of one or more files, set on the file itself (for example right after
 * an upload). They are independent of share links: a file nobody has a link to is still available
 * to the datastore's guests once it is visible.
 */
export function FileAccessDialog({ open, onOpenChange, files = [], justUploaded = false }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(DEFAULTS);
  const [datastore, setDatastore] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [policyOpen, setPolicyOpen] = useState(false);
  const many = files.length > 1;

  useEffect(() => {
    if (!open || files.length === 0) return undefined;
    let active = true;
    setError('');
    setForm(DEFAULTS);
    getFileCheckoutAccess(files[0].id)
      .then(({ data }) => {
        if (!active) return;
        setDatastore({ name: data.datastore, ...data.datastore_settings });
        if (!many) setForm({ visible: data.visible, requires_approval: data.requires_approval, requires_nda: data.requires_nda, allow_download: data.allow_download });
      })
      .catch(() => {});
    return () => { active = false; };
  }, [open, files, many]);

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });
  const setTri = (key) => (e) => setForm({ ...form, [key]: TRI[e.target.value] });

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      await Promise.all(files.map((f) => saveFileCheckoutAccess(f.id, form)));
      toast.success(t('fileAccess.saved', { count: files.length, defaultValue: 'Check-out access saved.' }));
      onOpenChange(false);
    } catch (err) {
      setError(err.response?.data?.detail || t('fileAccess.failed', { defaultValue: 'Could not save the settings.' }));
    } finally {
      setBusy(false);
    }
  };

  const notReady = datastore && (!datastore.enabled || datastore.guest_count === 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {justUploaded
              ? t('fileAccess.titleUploaded', { count: files.length, defaultValue: 'Set check-out access for the new file(s)' })
              : t('fileAccess.title', { defaultValue: 'Check-out access' })}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          <p className="truncate font-medium" data-testid="access-files">
            {many ? t('fileAccess.manyFiles', { count: files.length, defaultValue: '{{count}} files' }) : files[0]?.name}
          </p>
          <p className="text-muted-foreground">
            {t('fileAccess.hint', { defaultValue: 'These settings belong to the file. They have nothing to do with share links: the datastore\'s guests can open a visible file even if no link was ever created.' })}
          </p>

          <label className="flex items-center gap-2">
            <input type="checkbox" checked={form.visible} onChange={set('visible')} aria-label="Visible in Check Out" />
            {t('fileAccess.visible', { defaultValue: 'Visible to the datastore\'s guests' })}
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={form.requires_approval} onChange={set('requires_approval')} aria-label="Requires admin approval" />
            {t('fileAccess.approval', { defaultValue: 'Each request needs admin approval (experimental)' })}
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1">
              <span>{t('fileAccess.nda', { defaultValue: 'NDA' })}</span>
              <select aria-label="NDA" className="w-full rounded-md border bg-transparent p-2" value={triValue(form.requires_nda)} onChange={setTri('requires_nda')}>
                <option value="">Datastore default</option><option value="yes">Required</option><option value="no">Not required</option>
              </select>
            </label>
            <label className="space-y-1">
              <span>{t('fileAccess.download', { defaultValue: 'Download' })}</span>
              <select aria-label="Download" className="w-full rounded-md border bg-transparent p-2" value={triValue(form.allow_download)} onChange={setTri('allow_download')}>
                <option value="">Datastore default</option><option value="yes">Allowed</option><option value="no">Not allowed</option>
              </select>
            </label>
          </div>

          {!many && files[0] && (
            <Button type="button" variant="outline" size="sm" onClick={() => setPolicyOpen(true)}>
              {t('fileAccess.openPolicy', { defaultValue: 'Who can open it, password, NDA, protections...' })}
            </Button>
          )}

          {notReady && (
            <p role="note" className="rounded-md border border-amber-300 bg-amber-50 p-3 text-amber-900 dark:bg-amber-950/20 dark:text-amber-200">
              {!datastore.enabled
                ? t('fileAccess.notEnabled', { defaultValue: 'Check-out is not enabled for this datastore yet, so no guest can see anything.' })
                : t('fileAccess.noGuests', { defaultValue: 'This datastore has no guests yet.' })}{' '}
              <Link className="underline" to="/check-out-center">{t('fileAccess.openCenter', { defaultValue: 'Open the Check Out Center' })}</Link>
            </p>
          )}
          {error && <p role="alert" className="text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>{justUploaded ? t('fileAccess.later', { defaultValue: 'Later' }) : t('common.cancel', { defaultValue: 'Cancel' })}</Button>
            <Button onClick={save} disabled={busy}>{t('common.save', { defaultValue: 'Save' })}</Button>
          </div>
        </div>
      </DialogContent>
      {files[0] && (
        <AccessPolicyDialog open={policyOpen} onOpenChange={setPolicyOpen} kind="dataroom-document" ident={files[0].id} label={files[0].name} />
      )}
    </Dialog>
  );
}
