import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { FileText, LogIn } from 'lucide-react';
import { FileRequestForm } from '../components/checkout/FileRequestForm';
import { Button } from '../components/ui/Button';
import { LoginNotice } from '../components/common/LoginNotice';

/** The public index: sign in to Coneshare, or request access to files. */
export function LandingPage() {
  const { t } = useTranslation();
  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      <div className="mx-auto max-w-4xl px-4 py-16">
        <h1 className="text-3xl font-bold tracking-tight">Coneshare</h1>
        <p className="mt-2 text-muted-foreground">
          {t('landing.subtitle', { defaultValue: 'Secure document sharing.' })}
        </p>
        <div className="mt-6"><LoginNotice /></div>
        <div className="mt-6 grid gap-6 md:grid-cols-2">
          <section className="space-y-4 rounded-xl border bg-white p-6 shadow-sm dark:bg-gray-800" aria-labelledby="landing-signin">
            <LogIn className="h-6 w-6" aria-hidden="true" />
            <h2 id="landing-signin" className="text-lg font-semibold">{t('landing.signIn', { defaultValue: 'Sign in' })}</h2>
            <p className="text-sm text-muted-foreground">
              {t('landing.signInText', { defaultValue: 'Manage documents, datarooms and share links.' })}
            </p>
            <Button asChild><Link to="/login">{t('landing.signInButton', { defaultValue: 'Sign in to Coneshare' })}</Link></Button>
          </section>
          <section className="space-y-4 rounded-xl border bg-white p-6 shadow-sm dark:bg-gray-800" aria-labelledby="landing-files">
            <FileText className="h-6 w-6" aria-hidden="true" />
            <h2 id="landing-files" className="text-lg font-semibold">{t('landing.fileRequest', { defaultValue: 'File request' })}</h2>
            <FileRequestForm />
          </section>
        </div>
      </div>
    </div>
  );
}
