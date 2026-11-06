import { useEffect, useState } from 'react';
import { getLoginNotice } from '../../services/api';

/** The monitoring notice shown before anyone signs in. Admin-configurable (Security settings); shown unless switched off. */
export function LoginNotice() {
  const [notice, setNotice] = useState(null);
  useEffect(() => {
    let live = true;
    Promise.resolve().then(() => getLoginNotice()).then((r) => { if (live) setNotice(r.data); }).catch(() => {});
    return () => { live = false; };
  }, []);
  if (!notice || !notice.enabled || !notice.text) return null;
  return (
    <div role="note" data-testid="login-notice"
         className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100">
      {notice.text}
    </div>
  );
}
