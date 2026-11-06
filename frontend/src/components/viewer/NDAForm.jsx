import { useState } from 'react';
import { toast } from 'sonner';
import { ShieldAlert, ArrowRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { acceptShareLinkNda } from '../../services/api';
import { getLocalizedErrorMessage } from '../../utils/errorTranslator';
import { Button } from '../ui/Button';
import { GateLayout } from './GateLayout';
import { useBranding } from '../../contexts/BrandingProvider';
import { cn } from '../../lib/utils';
import { NdaLink } from '../common/NdaLink';

export function NDAForm({ slug, onSuccess, publicMeta = null }) {
  const { t } = useTranslation();
  const { brandWebsiteUrl } = useBranding();
  const [agreed, setAgreed] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  const hasNdaText = Boolean(publicMeta?.nda_text && publicMeta.nda_text.trim());
  const ndaText = hasNdaText
    ? publicMeta.nda_text
    : t('viewer.ndaTermsUnavailable', {
        defaultValue: 'No Non-Disclosure Agreement terms are currently available.',
      });

  const versionString = publicMeta?.nda_version
    ? String(publicMeta.nda_version).startsWith('v')
      ? publicMeta.nda_version
      : `v${publicMeta.nda_version}`
    : null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!hasNdaText) {
      return;
    }
    if (!agreed) {
      toast.error(t('viewer.mustAgreeNda'));
      return;
    }

    setIsLoading(true);
    try {
      const searchParams = new URLSearchParams(window.location.search);
      const viewSessionId = searchParams.get('view_session_id');

      const response = await acceptShareLinkNda(slug, {
        view_session_id: viewSessionId,
      });

      toast.success(getLocalizedErrorMessage(response.data?.message || 'NDA accepted successfully.'));
      onSuccess(response.data?.view_session_id);
    } catch (err) {
      toast.error(getLocalizedErrorMessage(err, 'viewer.ndaAcceptFailed'));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <GateLayout publicMeta={publicMeta} maxWidth="max-w-lg">
      <div className="space-y-4 pt-1">
        {/* NDA Warning Banner */}
        <div className="flex items-start gap-2.5 rounded-lg bg-indigo-50/90 dark:bg-indigo-950/30 p-3 text-xs text-indigo-950 dark:text-indigo-200 border border-indigo-200/80 dark:border-indigo-900/50">
          <ShieldAlert className="h-4 w-4 shrink-0 text-indigo-600 dark:text-indigo-400 mt-0.5" />
          <div className="leading-relaxed">
            <span className="font-semibold text-[11px] block uppercase tracking-wider text-indigo-900 dark:text-indigo-300">
              {t('viewer.ndaRequired')}
            </span>
            {t('viewer.ndaNotice')}
          </div>
        </div>

        {/* Legal Agreement Viewer Box */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] font-medium text-gray-500 dark:text-zinc-400 px-0.5">
            <span>
              {versionString
                ? t('viewer.confidentialityAgreementWithVersion', {
                    version: versionString,
                    defaultValue: `Confidentiality Agreement (${versionString})`,
                  })
                : t('viewer.confidentialityAgreement', {
                    defaultValue: 'Confidentiality & Non-Disclosure Agreement',
                  })}
            </span>
          </div>
          <div
            className={cn(
              'max-h-52 overflow-y-auto overflow-x-hidden rounded-xl border border-gray-200 dark:border-zinc-800 bg-gray-50 dark:bg-zinc-900/50 p-4 text-xs leading-relaxed shadow-inner nda-scroll whitespace-pre-line break-words',
              hasNdaText
                ? 'text-gray-700 dark:text-zinc-300'
                : 'text-amber-800 dark:text-amber-400/90 italic bg-amber-50/40 dark:bg-amber-950/10 border-amber-200/70 dark:border-amber-900/40'
            )}
          >
            {ndaText}
          </div>
          <NdaLink className="px-0.5 text-xs" />
        </div>

        {/* Agreement Checkbox & Primary CTA */}
        <form onSubmit={handleSubmit} className="space-y-4 pt-1">
          <label
            className={cn(
              'flex items-start gap-3 select-none group',
              hasNdaText ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'
            )}
          >
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 dark:border-zinc-700 dark:bg-zinc-800 dark:focus:ring-offset-zinc-900 cursor-pointer disabled:cursor-not-allowed"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              disabled={isLoading || !hasNdaText}
            />
            <span className="text-xs text-gray-600 dark:text-zinc-400 group-hover:text-gray-900 dark:group-hover:text-zinc-200 transition-colors leading-normal">
              {t('viewer.ndaAgreeCheckbox')}
            </span>
          </label>

          <div className="pt-1">
            <Button
              type="submit"
              size="lg"
              className="w-full flex items-center justify-center gap-2 active:scale-[0.98] transition-all bg-gray-900 hover:bg-black text-white dark:bg-white dark:text-zinc-950 dark:hover:bg-zinc-200"
              disabled={isLoading || !agreed || !hasNdaText}
            >
              <span>{isLoading ? t('viewer.processing') : t('viewer.acceptAndView')}</span>
              {!isLoading && <ArrowRight className="h-4 w-4 shrink-0" />}
            </Button>
          </div>
        </form>

        <div className="text-center pt-1">
          <a
            href={brandWebsiteUrl || 'https://www.coneshare.com'}
            className="text-[11px] text-gray-400 dark:text-zinc-500 hover:text-gray-600 dark:hover:text-zinc-300 transition-colors"
          >
            {t('viewer.declineNda', { defaultValue: 'Decline and leave' })}
          </a>
        </div>
      </div>
    </GateLayout>
  );
}
