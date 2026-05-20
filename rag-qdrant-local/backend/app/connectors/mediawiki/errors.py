"""Exception hierarchy for the MediaWiki connector.

These bubble up from the importer / normalizer / service layer and are
caught at the API endpoint to produce structured error responses. Keep
the hierarchy shallow — granular subtypes only for cases the caller
actually distinguishes.
"""

from __future__ import annotations


class MediaWikiError(RuntimeError):
    """Base class for every connector failure."""


class MediaWikiXMLError(MediaWikiError):
    """Malformed XML, missing required elements, unsupported export schema."""


class MediaWikiConfigError(MediaWikiError):
    """Bad wiki config: missing base_url, invalid article_path, …"""


class MediaWikiUploadsError(MediaWikiError):
    """Uploads directory missing, unreadable, outside the allowed bases."""
