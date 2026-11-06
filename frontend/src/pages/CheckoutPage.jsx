import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight } from 'lucide-react';
import {
  acceptCheckoutNda,
  consumeCheckoutLink,
  endCheckoutSession,
  getCheckoutDatarooms,
  openCheckout,
  peekCheckoutLink,
  requestCheckoutFile,
} from '../services/api';
import { FileRequestForm } from '../components/checkout/FileRequestForm';
import { LoginNotice } from '../components/common/LoginNotice';
import { NdaLink } from '../components/common/NdaLink';
import { Button } from '../components/ui/Button';

const formatSize = (bytes) => {
  if (bytes == null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
};

const POLL_MS = 15000;

function NdaGate({ room, onAccepted }) {
  const { t } = useTranslation();
  const [agree, setAgree] = useState(false);
  const [error, setError] = useState('');
  const accept = async () => {
    try {
      await acceptCheckoutNda(room.id);
      onAccepted();
    } catch {
      setError(t('checkout.ndaFailed', { defaultValue: 'Could not record your agreement. Try again.' }));
    }
  };
  return (
    <div className="space-y-3 rounded-md border bg-amber-50 p-4 dark:bg-amber-950/20" data-testid="nda-gate">
      <h4 className="font-medium">{t('checkout.nda', { defaultValue: 'Non-disclosure agreement' })}</h4>
      <p className="max-h-48 overflow-auto whitespace-pre-wrap text-sm">{room.nda.text}</p>
      <NdaLink />
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
        {t('checkout.ndaAgree', { defaultValue: 'I have read and agree to this agreement' })}
      </label>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      <Button size="sm" disabled={!agree} onClick={accept}>{t('checkout.ndaAccept', { defaultValue: 'Accept and continue' })}</Button>
    </div>
  );
}

function FileRow({ file, room, onChange, onError }) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  const guarded = async (fn) => {
    onError('');
    try {
      return await fn();
    } catch (err) {
      onError(err.response?.data?.detail || t('checkout.failed', { defaultValue: 'That did not work.' }));
      if (err.response?.data?.code === 'nda_required' || err.response?.status === 401) onChange();
      return null;
    }
  };

  const request = async () => { if (await guarded(() => requestCheckoutFile(file.id))) onChange(); };
  const open = async () => {
    const result = await guarded(() => openCheckout(file.checkout_id));
    if (result) navigate(`/checkout/view/${file.checkout_id}`);
  };

  let action;
  if (file.state === 'pending') {
    action = <span className="text-sm text-amber-700">{t('checkout.waiting', { defaultValue: 'Waiting for approval' })}</span>;
  } else if (file.state === 'approved') {
    action = <Button size="sm" onClick={open}>{t('checkout.open', { defaultValue: 'Open' })}</Button>;
  } else if (file.state === 'open') {
    action = <Button size="sm" asChild><Link to={`/checkout/view/${file.checkout_id}`}>{t('checkout.continue', { defaultValue: 'Continue viewing' })}</Link></Button>;
  } else if (file.state === 'denied') {
    action = <span className="text-sm text-red-700">{t('checkout.denied', { defaultValue: 'Denied' })}</span>;
  } else {
    action = (
      <Button size="sm" onClick={request} disabled={room.attempts_remaining < 1 || (file.requires_nda && !room.nda.accepted)}>
        {file.requires_approval
          ? t('checkout.requestApproval', { defaultValue: 'Request (needs approval)' })
          : t('checkout.request', { defaultValue: 'Request' })}
      </Button>
    );
  }

  return (
    <li className="flex items-center justify-between gap-3 p-3">
      <div className="min-w-0">
        <div className="truncate font-medium">{file.name}</div>
        <div className="text-xs text-gray-500">{formatSize(file.size)}</div>
      </div>
      <div className="shrink-0">{action}</div>
    </li>
  );
}

function Dataroom({ room, onChange, onError }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const locked = room.nda.required && !room.nda.accepted;
  return (
    <li className="rounded-md border" data-testid={`dataroom-${room.id}`}>
      <button type="button" className="flex w-full items-center justify-between gap-3 p-3 text-left"
              aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
        <span className="flex items-center gap-2 font-medium">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          {room.name}
        </span>
        <span className="text-xs text-gray-500">
          {t('checkout.fileCount', { count: room.files.length, defaultValue: '{{count}} file(s)' })} ·{' '}
          {t('checkout.attempts', { left: room.attempts_remaining, max: room.attempts_max, defaultValue: '{{left}} of {{max}} requests left' })}
        </span>
      </button>
      {expanded && (
        <div className="space-y-3 border-t p-3">
          {locked && <NdaGate room={room} onAccepted={onChange} />}
          {room.files.length === 0 ? (
            <p className="text-sm text-gray-500">{t('checkout.none', { defaultValue: 'No files are available here yet.' })}</p>
          ) : (
            <ul className="divide-y rounded-md border" data-testid={`files-${room.id}`}>
              {room.files.map((file) => <FileRow key={file.id} file={file} room={room} onChange={onChange} onError={onError} />)}
            </ul>
          )}
        </div>
      )}
    </li>
  );
}

/** /checkout: sign in with an e-mailed link, then see the datastores you were given and request files. */
export function CheckoutPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const location = useLocation();
  // E-mailed links carry the token in the fragment (#token=...), which is never sent to a server, written to an
  // access log or put in a Referer; links from older e-mails used the query. Either way it is taken once and
  // removed from the address bar straight away, so it is not left in history or on screen.
  const [token] = useState(() => new URLSearchParams((location.hash || '').replace(/^#/, '')).get('token') || params.get('token'));
  useEffect(() => {
    if (token && (location.hash || location.search)) navigate('/checkout', { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);
  const [phase, setPhase] = useState('loading'); // loading | confirm | invalid | anonymous | portal
  const [maskedEmail, setMaskedEmail] = useState('');
  const [data, setData] = useState({ email: '', datarooms: [] });
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const response = await getCheckoutDatarooms();
      setData(response.data);
      setPhase('portal');
    } catch {
      setPhase('anonymous');
    }
  }, []);

  useEffect(() => {
    if (!token) { load(); return; }
    peekCheckoutLink(token)
      .then(({ data: body }) => { setMaskedEmail(body.email); setPhase('confirm'); })
      .catch(() => setPhase('invalid'));
  }, [token, load]);

  const waiting = phase === 'portal' && data.datarooms.some((room) => room.files.some((f) => f.state === 'pending'));
  useEffect(() => {
    if (!waiting) return undefined;
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [waiting, load]);

  const continueWithLink = async () => {
    try {
      await consumeCheckoutLink(token);
      navigate('/checkout', { replace: true }); // the token leaves the address bar
    } catch {
      setPhase('invalid');
    }
  };

  const signOut = async () => {
    try { await endCheckoutSession(); } catch { /* ignore */ }
    setPhase('anonymous');
  };

  if (phase === 'loading') return <div className="p-8 text-center text-sm text-gray-500">…</div>;

  return (
    <div className="mx-auto max-w-2xl p-6">
      <h1 className="text-2xl font-semibold">{t('checkout.title', { defaultValue: 'File request' })}</h1>
      <div className="mt-4"><LoginNotice /></div>

      {phase === 'confirm' && (
        <div className="mt-6 space-y-3">
          <p>{t('checkout.confirm', { email: maskedEmail, defaultValue: 'Continue as {{email}}?' })}</p>
          <Button onClick={continueWithLink}>{t('checkout.continueButton', { defaultValue: 'Continue' })}</Button>
        </div>
      )}

      {phase === 'invalid' && (
        <div className="mt-6 space-y-4">
          <p role="alert" className="text-red-600">{t('checkout.invalid', { defaultValue: 'This link is invalid or has expired.' })}</p>
          <FileRequestForm />
        </div>
      )}

      {phase === 'anonymous' && <div className="mt-6"><FileRequestForm /></div>}

      {phase === 'portal' && (
        <div className="mt-6 space-y-4">
          <div className="flex items-center justify-between text-sm">
            <span>{t('checkout.signedInAs', { email: data.email, defaultValue: 'Signed in as {{email}}' })}</span>
            <Button variant="ghost" size="sm" onClick={signOut}>{t('checkout.signOut', { defaultValue: 'Sign out' })}</Button>
          </div>
          <h2 className="text-lg font-medium">{t('checkout.available', { defaultValue: 'Documents you can access' })}</h2>
          {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
          {data.datarooms.length === 0 ? (
            <p className="text-sm text-gray-500">{t('checkout.noRooms', { defaultValue: 'Nothing has been shared with you yet.' })}</p>
          ) : (
            <ul className="space-y-3" data-testid="datarooms">
              {data.datarooms.map((room) => <Dataroom key={room.id} room={room} onChange={load} onError={setError} />)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
