import { useTranslation } from 'react-i18next';

/**
 * Covers the viewer and asks the viewer to click back in while the page is not focused.
 * The real protection is not this cover: while unfocused the server stops serving pages and
 * the page canvases are emptied (see lib/viewingProtection.js); this is what the viewer sees.
 */
export function FocusBlurOverlay({ visible, onResume }) {
  const { t } = useTranslation();
  if (!visible) return null;

  return (
    <div
      role="dialog"
      aria-label={t('viewer.clickToResume', { defaultValue: 'Click to resume viewing' })}
      data-testid="focus-blur-overlay"
      className="fixed inset-0 flex items-center justify-center"
      style={{ zIndex: 80 }}
      onClick={onResume}
    >
      <div
        className="absolute inset-0"
        style={{ backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)', backgroundColor: 'rgba(0, 0, 0, 0.55)' }}
      />
      <div className="relative rounded-xl bg-white px-8 py-6 text-center shadow-2xl dark:bg-gray-900">
        <p className="text-base font-medium text-gray-900 dark:text-gray-100">
          {t('viewer.clickToResume', { defaultValue: 'Please click on the window to preview this sensitive file.' })}
        </p>
      </div>
    </div>
  );
}
