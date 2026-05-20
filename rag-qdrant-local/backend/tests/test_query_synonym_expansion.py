"""Query-time synonym expansion — eval-driven, one entry per measured gap."""

from __future__ import annotations

from app.retrieval_service import _expand_query_with_synonyms


def test_dienstleister_query_gets_lieferanten_appended():
    out = _expand_query_with_synonyms(
        "Welche Sicherheitsanforderungen gelten für externe Dienstleister?"
    )
    assert "Lieferanten" in out
    # Original question text must remain so any LLM downstream still sees it.
    assert "Dienstleister" in out


def test_lieferanten_query_gets_dienstleister_appended():
    out = _expand_query_with_synonyms(
        "Wer pflegt die Lieferantenliste?"
    )
    assert "Dienstleister" in out
    assert "Lieferanten" in out


def test_no_expansion_when_both_terms_already_present():
    """Idempotent — re-running on already-expanded text adds nothing."""
    original = "Externe Dienstleister und Lieferanten brauchen Zugang."
    out = _expand_query_with_synonyms(original)
    assert out == original


def test_no_expansion_on_unrelated_query():
    """Queries that don't trigger any synonym pair must come back unchanged."""
    original = "Wie ist die Backup-Strategie laut der Backup-Richtlinie geregelt?"
    assert _expand_query_with_synonyms(original) == original


def test_empty_question_passthrough():
    assert _expand_query_with_synonyms("") == ""


def test_expansion_is_idempotent():
    """Running expansion twice in a row yields the same string."""
    original = "Welche Sicherheitsanforderungen gelten für externe Dienstleister?"
    once = _expand_query_with_synonyms(original)
    twice = _expand_query_with_synonyms(once)
    assert once == twice


def test_prefix_match_picks_up_german_compounds_and_declensions():
    """German compound nouns (``Lieferantenliste``) and declensions
    (``Dienstleistern``) should trigger expansion — they share the stem
    with the canonical form."""
    assert "Dienstleister" in _expand_query_with_synonyms("Lieferantenliste prüfen?")
    assert "Lieferanten" in _expand_query_with_synonyms(
        "Welche Anforderungen gelten für Dienstleistern in der Cloud?"
    )


def test_prefix_match_does_not_fire_on_unrelated_german_words():
    """``Lieferung`` (delivery) and ``Dienstleistung`` (abstract service)
    share initial letters with the triggers but are different concepts —
    must not be expanded."""
    assert _expand_query_with_synonyms(
        "Wann erfolgt die nächste Lieferung?"
    ) == "Wann erfolgt die nächste Lieferung?"
    assert _expand_query_with_synonyms(
        "Welche Dienstleistung wird angeboten?"
    ) == "Welche Dienstleistung wird angeboten?"
