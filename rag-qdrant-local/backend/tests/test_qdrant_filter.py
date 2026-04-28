"""QdrantStore.search() must enforce tenant + project filters."""

import pytest

from app.qdrant_store import QdrantStore, QdrantStoreError


def test_search_refuses_empty_tenant():
    store = QdrantStore.__new__(QdrantStore)  # don't connect
    store.collection = "dummy"
    store.url = "http://nowhere"
    store.api_key = None
    store.client = None  # type: ignore[assignment]

    with pytest.raises(QdrantStoreError, match="non-empty tenant"):
        store.search(
            tenant="",
            project="p",
            query_vector=[0.0] * 8,
            top_k=1,
        )


def test_search_refuses_empty_project():
    store = QdrantStore.__new__(QdrantStore)
    store.collection = "dummy"
    store.url = "http://nowhere"
    store.api_key = None
    store.client = None  # type: ignore[assignment]

    with pytest.raises(QdrantStoreError, match="non-empty tenant"):
        store.search(
            tenant="t",
            project="",
            query_vector=[0.0] * 8,
            top_k=1,
        )
