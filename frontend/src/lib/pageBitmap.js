/**
 * Decode a page image Blob into something that can be painted onto a canvas.
 *
 * The decoded bitmap lives only in memory: there is never a URL for the image in the DOM
 * (no <img src>, no blob: link that could be opened or saved from the page).
 */
export async function blobToBitmap(blob) {
  if (typeof createImageBitmap === 'function') {
    return createImageBitmap(blob);
  }

  // Fallback for browsers without createImageBitmap: decode through an Image using a
  // temporary object URL that is revoked as soon as decoding finishes.
  const objectUrl = URL.createObjectURL(blob);
  try {
    const image = new Image();
    image.decoding = 'async';
    image.src = objectUrl;
    await image.decode();
    return { width: image.naturalWidth, height: image.naturalHeight, source: image, close() {} };
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export function paintBitmap(canvas, bitmap) {
  const context = canvas.getContext('2d');
  canvas.width = bitmap.width;
  canvas.height = bitmap.height;
  context.drawImage(bitmap.source || bitmap, 0, 0);
}
