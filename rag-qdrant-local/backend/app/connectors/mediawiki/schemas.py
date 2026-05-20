"""Pydantic models and shared dataclasses for the MediaWiki connector.

The pydantic models are used by the API endpoint and the CLI for I/O
validation. The dataclasses are the internal data passed between the
importer, normalizer, uploads resolver, and service — they have no
serialisation responsibility, so dataclasses keep them lightweight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from pydantic import BaseModel, Field

from ...schemas import TenantProject


# ---------------------------------------------------------------------------
# API I/O models
# ---------------------------------------------------------------------------

class MediaWikiWikiConfig(BaseModel):
    """How to reconstruct page URLs for the imported wiki."""

    base_url: str = Field(
        min_length=1,
        description="e.g. 'https://wiki.demo.local'. Trailing slash is stripped.",
    )
    article_path: str = Field(
        default="/wiki/$1",
        description="MediaWiki ``$wgArticlePath`` — '$1' is the title placeholder.",
    )
    script_path: str = Field(
        default="/w",
        description="MediaWiki ``$wgScriptPath``. Not used for page URLs but "
                    "kept for File-page URL construction.",
    )


class ImportXMLRequest(TenantProject):
    xml_path: str = Field(min_length=1)
    uploads_path: Optional[str] = Field(default=None)
    wiki: MediaWikiWikiConfig
    allowed_namespaces: List[int] = Field(default_factory=lambda: [0])
    include_redirects: bool = False
    include_uploads: bool = True
    reindex_changed_only: bool = True
    dry_run: bool = False


class ImportXMLResponse(BaseModel):
    status: str
    mode: str = "xml"
    dry_run: bool
    pages_seen: int
    pages_indexed: int
    pages_skipped_namespace: int
    pages_skipped_redirect: int
    pages_skipped_unchanged: int
    files_seen: int
    files_indexed: int
    files_skipped_unsupported: int
    files_skipped_unchanged: int = 0
    unresolved_files: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MediaWikiFileRef:
    """One ``[[File:...]]`` reference found inside a page."""

    title: str                    # "File:Netzplan.pdf" or "Datei:Netzplan.pdf"
    bare_filename: str            # "Netzplan.pdf"


@dataclass
class MediaWikiPage:
    """One MediaWiki page (latest revision) — produced by the importer,
    consumed by the normalizer and service."""

    page_id: int
    title: str
    namespace_id: int
    namespace_name: Optional[str]
    revision_id: int
    revision_timestamp: Optional[str]   # ISO-8601 or None
    raw_text: str
    is_redirect: bool
    redirect_target: Optional[str] = None


@dataclass
class NormalizedPage:
    """Output of the wikitext normalizer."""

    text: str                          # cleaned-up markdown-ish prose
    categories: List[str]              # extracted from [[Category:…]] / [[Kategorie:…]]
    linked_files: List[MediaWikiFileRef]
