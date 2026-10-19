"""Spoken-text transforms: what the TTS reads aloud (WIN-586).

Registered on the TTS service via Pipecat's ``add_text_transformer`` hook, which runs on the
aggregated sentence *after* the display (``AggregatedTextFrame``) has been pushed, so the
transcript keeps the full markdown and only the synthesized text changes.
"""

import re

_IMAGE = re.compile(r"!\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\([^)\s]*(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<(?:https?|ftp)://[^>\s]+>")
_BARE_URL = re.compile(r"(?:https?|ftp)://[^\s<>()\[\]]*[^\s<>()\[\].,;:!?'\"]")


def strip_links_for_speech(text: str) -> str:
    """[label](url) -> label; ![alt](url) -> alt; <url> and bare URLs -> "link"."""
    text = _IMAGE.sub(lambda m: m.group(1), text)
    text = _LINK.sub(lambda m: m.group(1), text)
    text = _AUTOLINK.sub("link", text)
    return _BARE_URL.sub("link", text)


async def strip_links_transform(text: str, aggregation_type: str) -> str:
    """Pipecat ``text_transforms`` signature."""
    return strip_links_for_speech(text)
