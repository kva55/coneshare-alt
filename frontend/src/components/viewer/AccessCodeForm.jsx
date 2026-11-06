import { useState } from 'react';
import { ArrowRight, Mail, KeyRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { requestShareLinkAccessCode, verifyShareLinkAccessCode } from '../../services/api';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';
import { GateLayout } from './GateLayout';

/**
 * Two-step gate for allow-listed links: (1) enter email -> a code is emailed if the address is
 * on the list; (2) enter that code. The first step shows the same message either way.
 */
export function AccessCodeForm({ slug, onSuccess, publicMeta = null }) {
  const { t } = useTranslation();
  const [step, setStep] = useState('email');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);

  const requestCode = async (e) => {
    e?.preventDefault();
    setIsLoading(true);
    setError('');
    try {
      const { data } = await requestShareLinkAccessCode(slug, email.trim());
      setMessage(data.message);
      setStep('code');
    } catch (err) {
      setError(err.response?.data?.email?.[0] || t('viewer.accessRequestFailed', { defaultValue: 'Could not send the request. Please try again.' }));
    } finally {
      setIsLoading(false);
    }
  };

  const verifyCode = async (e) => {
    e.preventDefault();
    setIsLoading(true);
    setError('');
    try {
      await verifyShareLinkAccessCode(slug, email.trim(), code.trim());
      onSuccess();
    } catch (err) {
      setError(err.response?.data?.message || t('viewer.accessCodeInvalid', { defaultValue: 'Invalid or expired access code.' }));
      setIsLoading(false);
    }
  };

  return (
    <GateLayout publicMeta={publicMeta} maxWidth="max-w-md">
      <div className="space-y-4 pt-1">
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white tracking-tight">
            {t('viewer.accessRestricted', { defaultValue: 'Restricted access' })}
          </h2>
          <p className="text-xs text-gray-500 dark:text-zinc-400 mt-1 leading-relaxed">
            {step === 'email'
              ? t('viewer.accessEmailSubtitle', { defaultValue: 'Enter your email address to request access.' })
              : message}
          </p>
        </div>

        {step === 'email' ? (
          <form onSubmit={requestCode} className="space-y-3.5">
            <div className="space-y-1.5">
              <Label htmlFor="access-email" className="block text-xs font-medium text-gray-700 dark:text-zinc-300">
                {t('auth.email', { defaultValue: 'Email' })}
              </Label>
              <div className="relative">
                <Mail className="absolute left-3 top-3 h-4 w-4 text-gray-400 z-10 pointer-events-none" />
                <Input
                  id="access-email"
                  type="email"
                  required
                  autoFocus
                  autoComplete="email"
                  className="pl-10"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>
            {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
            <Button type="submit" size="lg" className="w-full flex items-center justify-center gap-2" disabled={isLoading}>
              <span>{t('viewer.requestAccess', { defaultValue: 'Request access' })}</span>
              {!isLoading && <ArrowRight className="h-4 w-4 shrink-0" />}
            </Button>
          </form>
        ) : (
          <form onSubmit={verifyCode} className="space-y-3.5">
            <div className="space-y-1.5">
              <Label htmlFor="access-code" className="block text-xs font-medium text-gray-700 dark:text-zinc-300">
                {t('viewer.accessCode', { defaultValue: 'Access code' })}
              </Label>
              <div className="relative">
                <KeyRound className="absolute left-3 top-3 h-4 w-4 text-gray-400 z-10 pointer-events-none" />
                <Input
                  id="access-code"
                  required
                  autoFocus
                  autoComplete="one-time-code"
                  className="pl-10 font-mono tracking-widest uppercase"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                />
              </div>
            </div>
            {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
            <Button type="submit" size="lg" className="w-full flex items-center justify-center gap-2" disabled={isLoading}>
              <span>{isLoading ? t('viewer.verifying') : t('viewer.continue', { defaultValue: 'Continue' })}</span>
            </Button>
            <button
              type="button"
              className="w-full text-xs text-gray-500 underline"
              onClick={() => { setStep('email'); setCode(''); setError(''); }}
            >
              {t('viewer.useDifferentEmail', { defaultValue: 'Use a different email' })}
            </button>
          </form>
        )}
      </div>
    </GateLayout>
  );
}
