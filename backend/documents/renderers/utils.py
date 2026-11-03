import mimetypes
import os

from .constants import DANGEROUS_SCRIPT_EXTENSIONS


def normalize_content_type(content_type: str, filename: str = '') -> str:
    """Guesses MIME type if missing or application/octet-stream."""
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        # Prevent active web scripting extensions from adopting incompatible previewable content types
        if ext in DANGEROUS_SCRIPT_EXTENSIONS:
            guessed_type, _ = mimetypes.guess_type(filename)
            return guessed_type or 'application/octet-stream'

    if not content_type or content_type == 'application/octet-stream':
        if filename:
            guessed_type, _ = mimetypes.guess_type(filename)
            if guessed_type:
                return guessed_type
    return content_type or ''
