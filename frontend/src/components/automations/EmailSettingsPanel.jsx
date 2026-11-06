import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { getEmailSettings, saveEmailSettings, sendTestEmail, testEmailReceive } from '../../services/api';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';

const EMPTY = {
  enabled: false, host: '', port: 587, username: '', password: '', use_tls: true, use_ssl: false, from_email: '',
  allow_insecure: false, imap_host: '', imap_port: 993, imap_use_ssl: true, imap_username: '', imap_password: '', imap_test_address: '',
};

const firstError = (err) => {
  const data = err.response?.data;
  if (!data) return null;
  if (typeof data === 'string') return data;
  const first = Object.values(data)[0];
  return Array.isArray(first) ? first[0] : String(first);
};

/** E-mail setup, edited here instead of in server configuration. The password is write-only. */
export function EmailSettingsPanel() {
  const { t } = useTranslation();
  const [form, setForm] = useState(EMPTY);
  const [meta, setMeta] = useState({ source: 'environment', has_password: false, has_imap_password: false, environment_configured: false });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  const apply = ({ data }) => {
    setForm({ ...EMPTY, ...data, password: '' });
    setForm((f) => ({ ...f, imap_password: '' }));
    setMeta({ source: data.source, has_password: data.has_password, has_imap_password: data.has_imap_password, environment_configured: data.environment_configured });
  };

  useEffect(() => {
    getEmailSettings().then(apply).catch(() => {});
  }, []);

  const set = (key) => (e) =>
    setForm({ ...form, [key]: e.target.type === 'checkbox' ? e.target.checked : e.target.value });

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      apply(await saveEmailSettings({ ...form, port: Number(form.port), imap_port: Number(form.imap_port) }));
      toast.success(t('emailSettings.saved', { defaultValue: 'E-mail settings saved.' }));
    } catch (err) {
      setMessage(firstError(err) || t('emailSettings.saveFailed', { defaultValue: 'Could not save the settings.' }));
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    setBusy(true);
    setMessage('');
    try {
      const { data } = await sendTestEmail();
      setMessage(data.detail);
    } catch (err) {
      setMessage(err.response?.data?.detail || t('emailSettings.testFailed', { defaultValue: 'Sending failed.' }));
    } finally {
      setBusy(false);
    }
  };

  const testReceive = async () => {
    setBusy(true);
    setMessage(t('emailSettings.receiveWaiting', { defaultValue: 'Sending a test message and waiting for it to arrive (up to 20 seconds)…' }));
    try {
      const { data } = await testEmailReceive();
      setMessage(data.detail);
    } catch (err) {
      setMessage(err.response?.data?.detail || t('emailSettings.testFailed', { defaultValue: 'Sending failed.' }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={save} className="space-y-4 rounded-md border p-4" aria-label="E-mail setup">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">{t('emailSettings.title', { defaultValue: 'E-mail setup' })}</h2>
        <span className="text-xs text-muted-foreground" data-testid="email-source">
          {meta.source === 'database'
            ? t('emailSettings.fromDatabase', { defaultValue: 'Using the settings below' })
            : meta.environment_configured
              ? t('emailSettings.fromEnvironment', { defaultValue: 'Using the server configuration' })
              : t('emailSettings.notConfigured', { defaultValue: 'Not configured - e-mails cannot be sent' })}
        </span>
      </div>
      <p className="text-sm text-muted-foreground">
        {t('emailSettings.hint', { defaultValue: 'Used for access codes, invitations, check-out notices and "someone viewed my document" e-mails.' })}
      </p>

      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.enabled} onChange={set('enabled')} />
        {t('emailSettings.enable', { defaultValue: 'Use these settings instead of the server configuration' })}
      </label>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="space-y-1 sm:col-span-2">
          <Label htmlFor="smtp-host">{t('emailSettings.host', { defaultValue: 'SMTP host' })}</Label>
          <Input id="smtp-host" value={form.host} onChange={set('host')} placeholder="smtp.example.com" />
        </div>
        <div className="space-y-1">
          <Label htmlFor="smtp-port">{t('emailSettings.port', { defaultValue: 'Port' })}</Label>
          <Input id="smtp-port" type="number" min={1} max={65535} value={form.port} onChange={set('port')} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="smtp-user">{t('emailSettings.username', { defaultValue: 'Username' })}</Label>
          <Input id="smtp-user" autoComplete="off" value={form.username} onChange={set('username')} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="smtp-pass">{t('emailSettings.password', { defaultValue: 'Password' })}</Label>
          <Input
            id="smtp-pass"
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={set('password')}
            placeholder={meta.has_password ? '••••••••  (leave blank to keep)' : ''}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="smtp-from">{t('emailSettings.from', { defaultValue: 'Sender address' })}</Label>
          <Input id="smtp-from" type="email" value={form.from_email} onChange={set('from_email')} placeholder="noreply@example.com" />
        </div>
      </div>

      <div className="flex gap-6 text-sm">
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={form.use_tls} onChange={(e) => setForm({ ...form, use_tls: e.target.checked, use_ssl: e.target.checked ? false : form.use_ssl })} />
          {t('emailSettings.starttls', { defaultValue: 'STARTTLS' })}
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={form.use_ssl} onChange={(e) => setForm({ ...form, use_ssl: e.target.checked, use_tls: e.target.checked ? false : form.use_tls })} />
          {t('emailSettings.ssl', { defaultValue: 'SSL/TLS' })}
        </label>
      </div>

      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={form.allow_insecure} onChange={set('allow_insecure')} />
        <span>
          {t('emailSettings.allowInsecure', { defaultValue: 'Allow an unencrypted connection (lab or private networks only)' })}
          <span className="block text-xs text-muted-foreground">
            {t('emailSettings.allowInsecureHint', { defaultValue: 'Without this, STARTTLS or SSL/TLS is required, and passwords are never sent unencrypted. Certificates are always checked.' })}
          </span>
        </span>
      </label>

      <fieldset className="space-y-3 rounded-md border p-3">
        <legend className="px-1 text-sm font-medium">{t('emailSettings.inbox', { defaultValue: 'Inbox check (to confirm that mail arrives)' })}</legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-1 sm:col-span-2">
            <Label htmlFor="imap-host">{t('emailSettings.imapHost', { defaultValue: 'IMAP host' })}</Label>
            <Input id="imap-host" value={form.imap_host} onChange={set('imap_host')} placeholder="imap.example.com" />
          </div>
          <div className="space-y-1">
            <Label htmlFor="imap-port">{t('emailSettings.imapPort', { defaultValue: 'IMAP port' })}</Label>
            <Input id="imap-port" type="number" min={1} max={65535} value={form.imap_port} onChange={set('imap_port')} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="imap-user">{t('emailSettings.imapUser', { defaultValue: 'Inbox username' })}</Label>
            <Input id="imap-user" autoComplete="off" value={form.imap_username} onChange={set('imap_username')} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="imap-pass">{t('emailSettings.imapPass', { defaultValue: 'Inbox password' })}</Label>
            <Input id="imap-pass" type="password" autoComplete="new-password" value={form.imap_password} onChange={set('imap_password')}
                   placeholder={meta.has_imap_password ? '••••••••  (leave blank to keep)' : ''} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="imap-address">{t('emailSettings.imapAddress', { defaultValue: 'Test address (this inbox)' })}</Label>
            <Input id="imap-address" type="email" value={form.imap_test_address} onChange={set('imap_test_address')} placeholder="inbox@example.com" />
          </div>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={form.imap_use_ssl} onChange={set('imap_use_ssl')} />
          {t('emailSettings.imapSsl', { defaultValue: 'Use SSL/TLS for the inbox' })}
        </label>
      </fieldset>

      {message && <p role="status" className="text-sm">{message}</p>}

      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>{t('common.save', { defaultValue: 'Save' })}</Button>
        <Button type="button" variant="outline" onClick={test} disabled={busy}>
          {t('emailSettings.sendTest', { defaultValue: 'Send test e-mail to me' })}
        </Button>
        <Button type="button" variant="outline" onClick={testReceive} disabled={busy || !form.imap_host}>
          {t('emailSettings.receiveTest', { defaultValue: 'Test sending and receiving' })}
        </Button>
      </div>
    </form>
  );
}
