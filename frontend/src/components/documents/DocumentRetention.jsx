import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { updateDocument } from '../../services/api';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';

const toLocalInput = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
};

/**
 * When this file is permanently deleted. Not the same as a link's expiry: the link stops working, the
 * file stays; this removes the file, its links, access logs and reports. Overrides the global policy.
 */
export function DocumentRetention({ document, onSaved }) {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => { setValue(toLocalInput(document?.delete_after)); }, [document?.delete_after]);

  const save = async (next) => {
    setSaving(true);
    try {
      await updateDocument(document.id, { delete_after: next ? new Date(next).toISOString() : null });
      toast.success(next ? t('retention.saved', { defaultValue: 'Deletion date saved.' }) : t('retention.cleared', { defaultValue: 'Deletion date removed; the global policy applies.' }));
      onSaved?.();
    } catch { /* the global interceptor shows the reason */ } finally { setSaving(false); }
  };

  return (
    <section className="space-y-2 rounded-md border p-4" data-testid="document-retention">
      <h3 className="text-sm font-medium">{t('retention.fileTitle', { defaultValue: 'Delete this file permanently on' })}</h3>
      <p className="text-sm text-muted-foreground">
        {t('retention.fileHint', { defaultValue: 'Separate from link expiry. On this date the file, its links, access logs and reports are hard-deleted. Leave empty to follow the global Data retention policy (if one is set).' })}
      </p>
      <div className="flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor="delete-after">{t('retention.date', { defaultValue: 'Date and time' })}</Label>
          <Input id="delete-after" type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} />
        </div>
        <Button size="sm" disabled={saving || !value} onClick={() => save(value)}>{t('common.save', { defaultValue: 'Save' })}</Button>
        <Button size="sm" variant="outline" disabled={saving || !document?.delete_after} onClick={() => { setValue(''); save(''); }}>
          {t('retention.clear', { defaultValue: 'Remove date' })}
        </Button>
      </div>
    </section>
  );
}
