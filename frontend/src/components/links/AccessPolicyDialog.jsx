import { NdaTemplateButton } from '../common/NdaTemplateButton';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { getAccessPolicy, saveAccessPolicy } from '../../services/api';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '../ui/Dialog';

const EMPTY = {
  allowed: '', requires_email: false, requires_email_verification: false, password: '', has_password: false, clear_password: false,
  require_nda: false, nda_text: '', enable_watermark: false, watermark_text: '', blur_when_unfocused: false,
  restricted_viewing_pane: false, allow_download: null,
};

const splitTokens = (text) => [...new Set(text.split(/[\s,;]+/).map((e) => e.trim().toLowerCase()).filter(Boolean))];

function Check({ id, label, hint, checked, onChange }) {
  return (
    <label htmlFor={id} className="flex items-start gap-2 text-sm">
      <input id={id} type="checkbox" className="mt-1" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}{hint && <span className="block text-xs text-muted-foreground">{hint}</span>}</span>
    </label>
  );
}

/**
 * Access settings of a document or a datastore: who may open it, and how it is protected. They belong
 * to the file itself, so they apply to every link to it and to check-out, whether or not a link exists.
 * `kind`: 'document' | 'dataroom' | 'dataroom-document'.
 */
export function AccessPolicyDialog({ open, onOpenChange, kind, ident, label = '' }) {
  const { t } = useTranslation();
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open || !ident) return undefined;
    let active = true;
    setError('');
    getAccessPolicy(kind, ident)
      .then(({ data }) => active && setForm({
        ...EMPTY, ...data, allowed: [...(data.allowed_emails || []), ...(data.allowed_domains || []).map((d) => `@${d}`)].join(', '),
        password: '', clear_password: false,
      }))
      .catch(() => {});
    return () => { active = false; };
  }, [open, kind, ident]);

  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));

  const save = async () => {
    setBusy(true);
    setError('');
    const payload = {
      allowed: splitTokens(form.allowed),
      requires_email: form.requires_email, requires_email_verification: form.requires_email_verification,
      require_nda: form.require_nda, nda_text: form.nda_text,
      enable_watermark: form.enable_watermark, watermark_text: form.watermark_text,
      blur_when_unfocused: form.blur_when_unfocused, restricted_viewing_pane: form.restricted_viewing_pane,
      allow_download: form.allow_download,
    };
    if (form.clear_password) payload.clear_password = true;
    else if (form.password) payload.password = form.password;
    try {
      await saveAccessPolicy(kind, ident, payload);
      toast.success(t('accessPolicy.saved', { defaultValue: 'Access settings saved.' }));
      onOpenChange(false);
    } catch (err) {
      const data = err.response?.data;
      const first = data && typeof data === 'object' ? Object.values(data)[0] : null;
      setError((Array.isArray(first) ? first[0] : first) || t('accessPolicy.failed', { defaultValue: 'Could not save the settings.' }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('accessPolicy.title', { defaultValue: 'Access settings' })}{label ? `: ${label}` : ''}</DialogTitle>
        </DialogHeader>
        <div className="space-y-6 text-sm">
          <p className="text-muted-foreground">
            {t('accessPolicy.hint', { defaultValue: 'These settings belong to the file. Every link to it follows them, and so does check-out, whether or not a link has been created or shared.' })}
          </p>

          <section className="space-y-2">
            <Label htmlFor="policy-allowed">{t('accessPolicy.allowed', { defaultValue: 'Allowed people' })}</Label>
            <textarea id="policy-allowed" rows={3} className="w-full rounded-md border bg-transparent p-2" placeholder="alice@example.com, @partner.com"
                      value={form.allowed} onChange={(e) => set('allowed')(e.target.value)} />
            <p className="text-xs text-muted-foreground">
              {t('accessPolicy.allowedHint', { defaultValue: 'Leave empty: anyone with a link can open it. Add addresses and/or a domain such as @partner.com: only those people can open it, each with an access code e-mailed to them.' })}
            </p>
            <Check id="policy-email" label={t('accessPolicy.requireEmail', { defaultValue: 'Ask viewers for their e-mail address' })}
                   checked={form.requires_email} onChange={set('requires_email')} />
            <Check id="policy-verify" label={t('accessPolicy.verifyEmail', { defaultValue: 'Verify the address with an e-mailed link' })}
                   checked={form.requires_email_verification} onChange={set('requires_email_verification')} />
          </section>

          <section className="space-y-2">
            <Label htmlFor="policy-password">{t('accessPolicy.password', { defaultValue: 'Password' })}</Label>
            <Input id="policy-password" type="password" autoComplete="new-password" value={form.password}
                   placeholder={form.has_password ? '••••••••  (leave blank to keep)' : ''}
                   onChange={(e) => setForm((f) => ({ ...f, password: e.target.value, clear_password: false }))} />
            {form.has_password && (
              <Check id="policy-clear-password" label={t('accessPolicy.clearPassword', { defaultValue: 'Remove the password' })}
                     checked={form.clear_password} onChange={(v) => setForm((f) => ({ ...f, clear_password: v, password: v ? '' : f.password }))} />
            )}
          </section>

          <section className="space-y-2">
            <Check id="policy-nda" label={t('accessPolicy.nda', { defaultValue: 'Viewers must accept a non-disclosure agreement' })}
                   checked={form.require_nda} onChange={set('require_nda')} />
            {form.require_nda && (
              <>
                <NdaTemplateButton value={form.nda_text} onUse={set('nda_text')} />
                <textarea aria-label="NDA text" rows={4} className="w-full rounded-md border bg-transparent p-2" value={form.nda_text}
                          placeholder="Agreement text" onChange={(e) => set('nda_text')(e.target.value)} />
              </>
            )}
          </section>

          <section className="space-y-2">
            <Check id="policy-watermark" label={t('accessPolicy.watermark', { defaultValue: 'Watermark every page' })}
                   checked={form.enable_watermark} onChange={set('enable_watermark')} />
            {form.enable_watermark && (
              <Input aria-label="Watermark text" value={form.watermark_text} onChange={(e) => set('watermark_text')(e.target.value)}
                     placeholder="CONFIDENTIAL - {{email}} {{ip-address}}" />
            )}
            <Label htmlFor="policy-download">{t('accessPolicy.download', { defaultValue: 'Downloading' })}</Label>
            <select id="policy-download" className="rounded-md border bg-transparent p-2"
                    value={form.allow_download === false ? 'no' : ''} onChange={(e) => set('allow_download')(e.target.value === 'no' ? false : null)}>
              <option value="">Each link decides</option>
              <option value="no">Never allow on any link</option>
            </select>
          </section>

          <section className="space-y-2 rounded-md border p-3">
            <h3 className="font-medium">{t('accessPolicy.protections', { defaultValue: 'Experimental viewing protections' })}</h3>
            <p className="text-xs text-muted-foreground">
              {t('accessPolicy.protectionsHint', { defaultValue: 'The server does the blurring: pages are never sent clear. They make casual screenshots and photos harder; a viewer can still photograph their screen.' })}
            </p>
            <Check id="policy-blur" label={t('accessPolicy.blur', { defaultValue: 'Blur when the page is not in focus' })}
                   hint={t('accessPolicy.blurHint', { defaultValue: 'While it is not in focus the server stops sending pages and the viewer empties its canvases.' })}
                   checked={form.blur_when_unfocused} onChange={set('blur_when_unfocused')} />
            <Check id="policy-pane" label={t('accessPolicy.pane', { defaultValue: 'Restricted viewing pane' })}
                   hint={t('accessPolicy.paneHint', { defaultValue: 'Pages arrive blurred; only a small area around the cursor is sent clear, with a dense watermark on top.' })}
                   checked={form.restricted_viewing_pane} onChange={set('restricted_viewing_pane')} />
          </section>

          {error && <p role="alert" className="text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>{t('common.cancel', { defaultValue: 'Cancel' })}</Button>
            <Button onClick={save} disabled={busy}>{t('common.save', { defaultValue: 'Save' })}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
