import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { useTranslation } from 'react-i18next';
import {
  addAllowedEmails,
  getAllowedEmails,
  resendAllowedEmailInvitation,
  revokeAllowedDomain,
  revokeAllowedEmail,
} from '../../services/api';
import { Button } from '../ui/Button';
import { Label } from '../ui/Label';

export const splitEmails = (text) =>
  [...new Set(text.split(/[\s,;]+/).map((e) => e.trim().toLowerCase()).filter(Boolean))];

/**
 * Allow-list editor. Without `linkId` (creating a link) it only collects addresses into
 * `pendingText`; the parent submits them after the link exists. With `linkId` it manages the
 * live list. Rendered inside the link form, so every button is type="button".
 */
export function AllowedEmailsPanel({ linkId = null, pendingText = '', onPendingTextChange = () => {} }) {
  const { t } = useTranslation();
  const [entries, setEntries] = useState([]);
  const [domains, setDomains] = useState([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!linkId) return;
    try {
      const { data } = await getAllowedEmails(linkId);
      setEntries(data.results);
      setDomains(data.domains || []);
    } catch {
      /* handled by the global interceptor */
    }
  }, [linkId]);

  useEffect(() => { load(); }, [load]);

  const add = async () => {
    const emails = splitEmails(draft);
    if (!emails.length) return;
    setBusy(true);
    try {
      const { data } = await addAllowedEmails(linkId, emails);
      const count = data.added.length + data.reinstated.length;
      if (count) toast.success(t('allowList.added', { count, defaultValue: '{{count}} invitation(s) sent.' }));
      if (data.domains_added?.length) toast.success(t('allowList.domainsAdded', { count: data.domains_added.length, defaultValue: '{{count}} domain rule(s) added.' }));
      if (data.invalid.length) toast.error(t('allowList.invalid', { list: data.invalid.join(', '), defaultValue: 'Not valid: {{list}}' }));
      setDraft('');
      await load();
    } catch {
      /* handled by the global interceptor */
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (entry) => {
    setBusy(true);
    try {
      await revokeAllowedEmail(linkId, entry.id);
      await load();
    } catch {
      /* handled by the global interceptor */
    } finally {
      setBusy(false);
    }
  };

  const resend = async (entry) => {
    try {
      await resendAllowedEmailInvitation(linkId, entry.id);
      toast.success(t('allowList.resent', { defaultValue: 'Invitation sent again.' }));
      await load();
    } catch {
      /* handled by the global interceptor */
    }
  };

  const hint = t('allowList.hint', {
    defaultValue: 'Leave this empty and anyone with the link can open it, no email needed. Add addresses (each is invited, and must enter an emailed access code) and/or a domain such as @partner.com (anyone at that domain can request an access code). Once anything is listed, only those can open the link.',
  });

  const revokeDomain = async (entry) => {
    setBusy(true);
    try {
      await revokeAllowedDomain(linkId, entry.id);
      await load();
    } catch {
      /* handled by the global interceptor */
    } finally {
      setBusy(false);
    }
  };

  if (!linkId) {
    return (
      <div className="space-y-2">
        <Label htmlFor="allowed-emails">{t('allowList.title', { defaultValue: 'Allowed emails' })}</Label>
        <p className="text-sm text-muted-foreground">{hint}</p>
        <textarea
          id="allowed-emails"
          className="w-full rounded-md border bg-transparent p-2 text-sm"
          rows={3}
          value={pendingText}
          placeholder="alice@example.com, @partner.com"
          onChange={(e) => onPendingTextChange(e.target.value)}
        />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <Label htmlFor="allowed-emails">{t('allowList.title', { defaultValue: 'Allowed emails' })}</Label>
      <p className="text-sm text-muted-foreground">{hint}</p>
      <div className="flex gap-2">
        <textarea
          id="allowed-emails"
          className="flex-1 rounded-md border bg-transparent p-2 text-sm"
          rows={2}
          value={draft}
          placeholder="alice@example.com, @partner.com"
          onChange={(e) => setDraft(e.target.value)}
        />
        <Button type="button" onClick={add} disabled={busy || !splitEmails(draft).length}>
          {t('allowList.add', { defaultValue: 'Add & invite' })}
        </Button>
      </div>
      {domains.length > 0 && (
        <ul className="divide-y rounded-md border text-sm" data-testid="allowed-domains">
          {domains.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-2 px-3 py-2">
              <div className={entry.is_active ? '' : 'line-through text-muted-foreground'}>
                @{entry.domain}
                <span className="ml-2 text-xs text-muted-foreground">
                  {entry.is_active ? t('allowList.domainRule', { defaultValue: 'anyone at this domain' }) : t('allowList.revoked', { defaultValue: 'Revoked' })}
                </span>
              </div>
              {entry.is_active && (
                <Button type="button" variant="ghost" size="sm" onClick={() => revokeDomain(entry)} disabled={busy}>
                  {t('allowList.revoke', { defaultValue: 'Revoke' })}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {entries.length === 0 && domains.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t('allowList.empty', { defaultValue: 'Nothing listed: anyone with the link can open it.' })}</p>
      ) : entries.length === 0 ? null : (
        <ul className="divide-y rounded-md border text-sm" data-testid="allowed-list">
          {entries.map((entry) => (
            <li key={entry.id} className="flex items-center justify-between gap-2 px-3 py-2">
              <div className="min-w-0">
                <div className={`truncate ${entry.is_active ? '' : 'line-through text-muted-foreground'}`}>{entry.email}</div>
                <div className="text-xs text-muted-foreground">
                  {entry.is_active
                    ? entry.last_access_at
                      ? t('allowList.lastAccess', { date: new Date(entry.last_access_at).toLocaleString(), defaultValue: 'Last access {{date}}' })
                      : t('allowList.neverAccessed', { defaultValue: 'Never accessed' })
                    : t('allowList.revoked', { defaultValue: 'Revoked' })}
                </div>
              </div>
              {entry.is_active && (
                <div className="flex shrink-0 gap-1">
                  <Button type="button" variant="ghost" size="sm" onClick={() => resend(entry)}>
                    {t('allowList.resend', { defaultValue: 'Resend' })}
                  </Button>
                  <Button type="button" variant="ghost" size="sm" onClick={() => revoke(entry)} disabled={busy}>
                    {t('allowList.revoke', { defaultValue: 'Revoke' })}
                  </Button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
