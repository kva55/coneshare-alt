import { useTranslation } from 'react-i18next';
import { EmailSettingsPanel } from '../components/automations/EmailSettingsPanel';
import { WebhookManager } from '../components/automations/WebhookManager';
import { useUser } from '../contexts/UserProvider';

/** Notifications: e-mail setup and generic webhooks. Per-link "email me on view" is on each link. */
export function AutomationsPage() {
  const { t } = useTranslation();
  const { user } = useUser();
  const isAdmin = user?.role === 'admin';

  return (
    <div className="container mx-auto max-w-4xl space-y-8 p-4 sm:p-6">
      <div>
        <h1 className="text-2xl font-bold">{t('notifications.title', { defaultValue: 'Notifications' })}</h1>
        <p className="text-muted-foreground">
          {t('notifications.subtitle', { defaultValue: 'To be emailed when someone views a document, switch on "Email me" in that link\'s settings.' })}
        </p>
      </div>
      {isAdmin ? (
        <>
          <EmailSettingsPanel />
          <WebhookManager />
        </>
      ) : (
        <p className="text-sm text-muted-foreground">
          {t('notifications.adminOnly', { defaultValue: 'E-mail setup and webhooks are managed by an administrator.' })}
        </p>
      )}
    </div>
  );
}
