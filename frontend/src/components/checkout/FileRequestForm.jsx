import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { requestCheckoutLink } from '../../services/api';
import { Button } from '../ui/Button';
import { Input } from '../ui/Input';
import { Label } from '../ui/Label';

/** "Enter your email to see accessible documents": we e-mail a sign-in link (if the address has access). */
export function FileRequestForm() {
  const { t } = useTranslation();
  const [email, setEmail] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const { data } = await requestCheckoutLink(email.trim());
      setMessage(data.message);
    } catch {
      setError(t('fileRequest.failed', { defaultValue: 'Could not send the request. Please try again.' }));
    } finally {
      setBusy(false);
    }
  };

  if (message) {
    return (
      <div role="status" className="space-y-2 text-sm">
        <p>{message}</p>
        <p className="text-muted-foreground">
          {t('fileRequest.linkNote', { defaultValue: 'The link works once, for 15 minutes.' })}
        </p>
        <Button type="button" variant="ghost" size="sm" onClick={() => { setMessage(''); setEmail(''); }}>
          {t('fileRequest.another', { defaultValue: 'Use another address' })}
        </Button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <Label htmlFor="file-request-email">
        {t('fileRequest.prompt', { defaultValue: 'Enter your email to see accessible documents' })}
      </Label>
      <Input id="file-request-email" type="email" required autoComplete="email" value={email}
             onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      <Button type="submit" disabled={busy}>{t('fileRequest.send', { defaultValue: 'Email me a link' })}</Button>
    </form>
  );
}
