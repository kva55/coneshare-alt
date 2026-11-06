/**
 * Utility functions for printing documents from the viewer.
 *
 * Printing is only offered when the link allows downloads, and it prints the (watermarked,
 * when required) download. Pages themselves are never available as image URLs, so there is
 * no image-based print path.
 */

/**
 * Prints a PDF file by loading it into a hidden iframe.
 * @param {string} pdfUrl - The URL of the PDF document.
 */
export function printPdf(pdfUrl) {
  const iframe = document.createElement('iframe');
  iframe.style.position = 'fixed';
  iframe.style.right = '0';
  iframe.style.bottom = '0';
  iframe.style.width = '0';
  iframe.style.height = '0';
  iframe.style.border = '0';
  iframe.src = pdfUrl;

  document.body.appendChild(iframe);

  iframe.onload = () => {
    try {
      iframe.contentWindow.focus();

      const cleanup = () => {
        clearTimeout(fallbackTimeout);
        if (document.body.contains(iframe)) {
          document.body.removeChild(iframe);
        }
      };

      // Clean up immediately when print dialog closes, with a fallback timeout
      iframe.contentWindow.addEventListener('afterprint', cleanup, { once: true });
      const fallbackTimeout = setTimeout(cleanup, 10000);

      iframe.contentWindow.print();
    } catch (err) {
      console.error('Failed to print PDF directly inside iframe', err);
      if (document.body.contains(iframe)) {
        document.body.removeChild(iframe);
      }
    }
  };
}
