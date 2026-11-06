import { useTranslation } from 'react-i18next';
import { NDA_TEMPLATE } from '../../lib/ndaTemplate';

/** Fills the NDA text box with the standard wording (asks first if there is already text). */
export function NdaTemplateButton({ value, onUse }) {
  const { t } = useTranslation();
  const use = () => {
    if ((value || '').trim() && (value || '').trim() !== NDA_TEMPLATE.trim()
        && !window.confirm(t('nda.replaceConfirm', { defaultValue: 'Replace the current text with the standard template?' }))) return;
    onUse(NDA_TEMPLATE);
  };
  return (
    <button type="button" className="text-sm text-indigo-600 hover:underline" onClick={use} data-testid="nda-template">
      {t('nda.useTemplate', { defaultValue: 'Use the standard template' })}
    </button>
  );
}
