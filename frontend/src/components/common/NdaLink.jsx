import { useEffect, useState } from 'react';
import { getLoginNotice } from '../../services/api';

/** The administrator's link (for example the Acceptable Use Policy), shown in NDA windows. Opens in a new tab. */
export function NdaLink({ className = '' }) {
  const [link, setLink] = useState(null);
  useEffect(() => {
    let live = true;
    Promise.resolve().then(() => getLoginNotice()).then((r) => {
      const url = r?.data?.nda_link_url;
      if (live && url && /^https?:\/\//i.test(url)) setLink({ url, text: r.data.nda_link_text || url });
    }).catch(() => {});
    return () => { live = false; };
  }, []);
  if (!link) return null;
  return (
    <a href={link.url} target="_blank" rel="noopener noreferrer" data-testid="nda-link"
       className={`text-sm text-indigo-600 underline hover:text-indigo-800 ${className}`}>
      {link.text}
    </a>
  );
}
