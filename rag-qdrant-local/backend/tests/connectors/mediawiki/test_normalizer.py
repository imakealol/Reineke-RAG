"""Wikitext → text normalizer.

The normalizer's job is to produce readable prose for the embedder and
pull categories + file references into a metadata sidecar. Edge cases
that show up in real DACH wikis are the priority; full wikitext
fidelity is explicitly out of scope.
"""

from __future__ import annotations

from app.connectors.mediawiki.normalizer import normalize_wikitext


def _norm(text: str):
    return normalize_wikitext(text)


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------

def test_h2_heading():
    out = _norm("== Übersicht ==")
    assert "## Übersicht" in out.text


def test_h3_heading_with_trailing_whitespace():
    out = _norm("===   Topologie   ===")
    assert "### Topologie" in out.text


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def test_internal_link_bare():
    out = _norm("[[pfSense]] ist eine Firewall.")
    assert "pfSense ist eine Firewall." in out.text


def test_internal_link_with_label():
    out = _norm("[[pfSense|unsere Firewall-Lösung]] schützt das LAN.")
    assert "unsere Firewall-Lösung schützt das LAN." in out.text


def test_external_link_with_label():
    out = _norm("Siehe [https://example.invalid Beispiel] dazu.")
    assert "Siehe Beispiel dazu." in out.text


def test_external_link_without_label():
    out = _norm("Quelle: [https://example.invalid/x]")
    assert "Quelle: https://example.invalid/x" in out.text


# ---------------------------------------------------------------------------
# Categories — extracted as metadata, removed from prose
# ---------------------------------------------------------------------------

def test_german_category_extracted_and_removed():
    out = _norm("Vorspann.\n[[Kategorie:IT-Sicherheit]]\n[[Kategorie:Konzepte]]")
    assert out.categories == ["IT-Sicherheit", "Konzepte"]
    assert "Kategorie:" not in out.text
    assert "IT-Sicherheit" not in out.text  # remove from prose


def test_english_category_alias_supported():
    out = _norm("Body.\n[[Category:IT-Security]]")
    assert "IT-Security" in out.categories
    assert "Category:" not in out.text


def test_category_with_sortkey_uses_name_before_pipe():
    out = _norm("[[Kategorie:IT-Sicherheit|Firewall]]")
    assert out.categories == ["IT-Sicherheit"]


# ---------------------------------------------------------------------------
# File references — extracted as metadata, removed from prose
# ---------------------------------------------------------------------------

def test_german_datei_namespace_extracted():
    out = _norm("Anhang: [[Datei:netzplan-demo.pdf]]")
    assert len(out.linked_files) == 1
    assert out.linked_files[0].bare_filename == "netzplan-demo.pdf"
    assert out.linked_files[0].title == "Datei:netzplan-demo.pdf"
    assert "netzplan-demo.pdf" not in out.text


def test_file_with_thumb_and_caption_stripped_to_filename():
    out = _norm("[[File:Diagram.png|thumb|right|Caption text]]")
    assert out.linked_files[0].bare_filename == "Diagram.png"
    assert "Caption text" not in out.text  # caption goes with the file ref


def test_image_alias_supported():
    out = _norm("[[Image:logo.svg]]")
    assert out.linked_files[0].bare_filename == "logo.svg"


# ---------------------------------------------------------------------------
# Templates, comments, refs, magic words
# ---------------------------------------------------------------------------

def test_template_removed():
    out = _norm("Text. {{Hinweis|Achtung}} Mehr Text.")
    assert "Hinweis" not in out.text
    assert "Achtung" not in out.text
    assert "Text." in out.text
    assert "Mehr Text." in out.text


def test_nested_template_removed():
    out = _norm("A {{Outer|{{Inner|x}}}} B")
    assert "Outer" not in out.text
    assert "Inner" not in out.text
    assert "A" in out.text and "B" in out.text


def test_html_comment_removed():
    out = _norm("Visible. <!-- internal todo --> Also visible.")
    assert "internal todo" not in out.text
    assert "Visible." in out.text
    assert "Also visible." in out.text


def test_ref_tag_removed():
    out = _norm("Aussage<ref>Quelle 1</ref>.")
    assert "Quelle 1" not in out.text
    assert "Aussage" in out.text


def test_magic_word_removed():
    out = _norm("__NOTOC__\n== Inhalt ==\nText.")
    assert "__NOTOC__" not in out.text
    assert "## Inhalt" in out.text


# ---------------------------------------------------------------------------
# Emphasis + lists
# ---------------------------------------------------------------------------

def test_bold_and_italic():
    out = _norm("Das ist '''fett''' und ''kursiv''.")
    assert "**fett**" in out.text
    assert "*kursiv*" in out.text


def test_bullet_list_to_markdown_dash():
    out = _norm("* Erste\n* Zweite\n** verschachtelt")
    assert "- Erste" in out.text
    assert "- Zweite" in out.text
    assert "  - verschachtelt" in out.text


def test_numbered_list_to_markdown():
    out = _norm("# A\n# B")
    assert "1. A" in out.text
    assert "1. B" in out.text


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def test_simple_wikitable_to_markdown():
    wiki = (
        "{| class=\"wikitable\"\n"
        "! Quelle !! Ziel\n"
        "|-\n"
        "| LAN || Internet\n"
        "|-\n"
        "| Internet || DMZ\n"
        "|}"
    )
    out = _norm(wiki)
    assert "| Quelle | Ziel |" in out.text
    assert "| --- | --- |" in out.text
    assert "| LAN | Internet |" in out.text


# ---------------------------------------------------------------------------
# Entities + whitespace
# ---------------------------------------------------------------------------

def test_html_entities_decoded():
    out = _norm("&amp; und &auml;hnlich")
    assert "& und ähnlich" in out.text


def test_german_umlauts_preserved():
    out = _norm("Maßnahmen für Übersicht — Ärger")
    assert "Maßnahmen für Übersicht — Ärger" in out.text


def test_whitespace_collapsed_but_paragraphs_kept():
    out = _norm("Eins.\n\n\n\nZwei.\n   \nDrei.")
    # Multiple blank lines collapse to one — paragraphs survive.
    assert "Eins.\n\nZwei." in out.text
    assert "Drei." in out.text


def test_empty_input_returns_empty_record():
    out = _norm("")
    assert out.text == ""
    assert out.categories == []
    assert out.linked_files == []
