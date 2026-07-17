import hashlib
import re


def normalize_text(text: str) -> str:
    """
    Collapse whitespace before hashing so that pure re-flow/re-wrapping
    (which OCR/re-extraction can introduce even when nothing semantically
    changed) doesn't register as a content change.

    NOTE: this is deliberately shallow. It does NOT normalize things like
    "1.5 mmHg" vs "1.50 mmHg" or reword detection - see APPROACH.md
    decision log Q3 for what this does and doesn't catch, and why a
    one-word threshold change and a one-word typo fix currently look
    identical to this function.
    """
    return re.sub(r"\s+", " ", text or "").strip().lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
