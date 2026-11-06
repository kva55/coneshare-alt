import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { AdminNav } from '../components/admin/AdminNav';
import { Button } from '../components/ui/Button';
import { Input } from '../components/ui/Input';
import { Label } from '../components/ui/Label';
import { getRetentionLog, getRetentionSettings, runRetentionNow, saveRetentionSettings } from '../services/api';

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const PRESETS = [
  { label: '1 week', value: 1, unit: 'weeks' },
  { label: '1 month', value: 1, unit: 'months' },
  { label: '1 year', value: 1, unit: 'years' },
];

export function AdminRetentionPage() {
  const { t } = useTranslation();
  const [settings, setSettings] = useState(null);
  const [info, setInfo] = useState({ due_now: 0, cutoff: null });
  const [log, setLog] = useState([]);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await getRetentionSettings();
      setSettings(data.settings);
      setInfo({ due_now: data.due_now, cutoff: data.cutoff });
      setLog((await getRetentionLog()).data.results);
    } catch { /* handled by the global interceptor */ }
  }, []);
  useEffect(() => { load(); }, [load]);

  if (!settings) return <div className="p-8 text-sm text-muted-foreground">…</div>;
  const set = (patch) => setSettings({ ...settings, ...patch });

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await saveRetentionSettings(settings);
      setSettings(data.settings);
      setInfo({ due_now: data.due_now, cutoff: data.cutoff });
      toast.success(t('retention.policySaved', { defaultValue: 'Data retention saved.' }));
    } catch { /* handled by the global interceptor */ } finally { setSaving(false); }
  };

  const runNow = async () => {
    if (!window.confirm(t('retention.confirmRun', { defaultValue: 'Permanently delete every file that is due now? This cannot be undone.' }))) return;
    try {
      const { data } = await runRetentionNow();
      toast.success(`${data.deleted} deleted, ${data.failed} failed`);
      load();
    } catch { /* handled by the global interceptor */ }
  };

  return (
    <div className="container mx-auto space-y-6 p-4 sm:p-6" data-testid="retention-page">
      <AdminNav />
      <div>
        <h1 className="text-2xl font-bold">{t('retention.title', { defaultValue: 'Data retention' })}</h1>
        <p className="text-muted-foreground">
          {t('retention.subtitle', { defaultValue: 'Files are permanently deleted (hard delete) after the time you set. A date set on a single file always wins over this policy. Off by default.' })}
        </p>
      </div>

      <section className="space-y-3 rounded-md border p-4">
        <Label htmlFor="mode">{t('retention.mode', { defaultValue: 'Policy for files without their own date' })}</Label>
        <select id="mode" className="block rounded-md border bg-transparent p-2 text-sm" value={settings.mode} onChange={(e) => set({ mode: e.target.value })}>
          <option value="off">{t('retention.off', { defaultValue: 'No automatic deletion' })}</option>
          <option value="after_upload">{t('retention.afterUpload', { defaultValue: 'Delete a set time after upload' })}</option>
          <option value="yearly">{t('retention.yearly', { defaultValue: 'Delete everything on a date every year' })}</option>
        </select>

        {settings.mode === 'after_upload' && (
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <Button key={p.label} type="button" size="sm" variant={settings.after_value === p.value && settings.after_unit === p.unit ? 'default' : 'outline'}
                        onClick={() => set({ after_value: p.value, after_unit: p.unit })}>{p.label}</Button>
              ))}
            </div>
            <div className="flex items-end gap-2">
              <div className="space-y-1">
                <Label htmlFor="after-value">{t('retention.custom', { defaultValue: 'Custom: after' })}</Label>
                <Input id="after-value" type="number" min={1} max={3650} className="w-28" value={settings.after_value}
                       onChange={(e) => set({ after_value: Number(e.target.value) })} />
              </div>
              <select aria-label="Unit" className="rounded-md border bg-transparent p-2 text-sm" value={settings.after_unit} onChange={(e) => set({ after_unit: e.target.value })}>
                {['days', 'weeks', 'months', 'years'].map((u) => <option key={u} value={u}>{u}</option>)}
              </select>
            </div>
          </div>
        )}

        {settings.mode === 'yearly' && (
          <div className="flex items-end gap-2">
            <div className="space-y-1">
              <Label htmlFor="yearly-month">{t('retention.everyYearOn', { defaultValue: 'Every year on' })}</Label>
              <select id="yearly-month" className="block rounded-md border bg-transparent p-2 text-sm" value={settings.yearly_month} onChange={(e) => set({ yearly_month: Number(e.target.value) })}>
                {MONTHS.map((m, i) => <option key={m} value={i + 1}>{m}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="yearly-day">{t('retention.day', { defaultValue: 'Day' })}</Label>
              <Input id="yearly-day" type="number" min={1} max={31} className="w-20" value={settings.yearly_day} onChange={(e) => set({ yearly_day: Number(e.target.value) })} />
            </div>
          </div>
        )}
        {settings.mode === 'yearly' && (
          <p className="text-sm text-muted-foreground">{t('retention.yearlyHint', { defaultValue: 'On that day every file uploaded before it is deleted. Files uploaded the day before are included.' })}</p>
        )}

        <label htmlFor="overwrite" className="flex items-start gap-2 text-sm">
          <input id="overwrite" type="checkbox" className="mt-1" checked={settings.overwrite} onChange={(e) => set({ overwrite: e.target.checked })} />
          <span>
            {t('retention.overwrite', { defaultValue: 'Overwrite stored data before deleting it (NIST SP 800-88 Rev. 1 "Clear": one pass)' })}
            <span className="block text-muted-foreground">{t('retention.overwriteHint', { defaultValue: 'On SSDs and copy-on-write file systems an overwrite cannot be guaranteed to reach every copy; use storage encryption (crypto-erase) as well. See docs/fork/data-retention.md.' })}</span>
          </span>
        </label>

        <div className="flex items-center gap-3">
          <Button onClick={save} disabled={saving}>{t('common.save', { defaultValue: 'Save' })}</Button>
          <span className="text-sm" data-testid="due-now">{info.due_now} {t('retention.dueNow', { defaultValue: 'file(s) due for deletion now' })}</span>
          <Button variant="outline" onClick={runNow} disabled={info.due_now === 0}>{t('retention.runNow', { defaultValue: 'Delete them now' })}</Button>
        </div>
      </section>

      <section className="space-y-2" aria-label="Log">
        <h2 className="text-lg font-medium">{t('retention.log', { defaultValue: 'Deletion log' })}</h2>
        {log.length === 0 ? <p className="text-sm text-muted-foreground">-</p> : (
          <ul className="divide-y rounded-md border text-sm" data-testid="retention-log">
            {log.map((r) => (
              <li key={r.id} className="flex flex-wrap justify-between gap-2 p-2">
                <span className="truncate">{r.document_name} · {r.reason}{r.overwritten ? ' · overwritten' : ''}</span>
                <span className={r.ok ? 'text-muted-foreground' : 'text-red-600'}>{r.ok ? new Date(r.created_at).toLocaleString() : `failed: ${r.error}`}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
