"""Tenant/project resolution for the OpenAI-compatible endpoint."""

import pytest

from app.openai_compat import OpenAIRequestError, resolve_openai_request
from app.schemas import OpenAIChatCompletionRequest, OpenAIMessage


def _req(**overrides):
    base = dict(
        model="rag:mk:jonyx",
        messages=[OpenAIMessage(role="user", content="Hallo?")],
        stream=False,
    )
    base.update(overrides)
    return OpenAIChatCompletionRequest(**base)


def test_model_id_encodes_tenant_and_project():
    r = resolve_openai_request(_req(model="rag:mk:jonyx"))
    assert r.tenant == "mk"
    assert r.project == "jonyx"
    assert r.question == "Hallo?"


def test_extra_body_overrides_when_model_does_not_carry_them():
    r = resolve_openai_request(
        _req(
            model="qwen2.5:14b",  # no rag: prefix → use extra_body
            extra_body={"tenant": "mk", "project": "jonyx"},
        )
    )
    assert r.tenant == "mk"
    assert r.project == "jonyx"


def test_inline_fields_are_honoured():
    r = resolve_openai_request(
        _req(model="anything", tenant="mk", project="jonyx")
    )
    assert (r.tenant, r.project) == ("mk", "jonyx")


def test_uses_last_user_message():
    r = resolve_openai_request(
        _req(
            messages=[
                OpenAIMessage(role="system", content="ignore me"),
                OpenAIMessage(role="user", content="erste Frage"),
                OpenAIMessage(role="assistant", content="erste Antwort"),
                OpenAIMessage(role="user", content="zweite Frage"),
            ]
        )
    )
    assert r.question == "zweite Frage"


def test_stream_true_is_rejected():
    with pytest.raises(OpenAIRequestError, match="stream"):
        resolve_openai_request(_req(stream=True))


def test_missing_tenant_project_raises():
    with pytest.raises(OpenAIRequestError, match="tenant and project"):
        resolve_openai_request(_req(model="qwen2.5:14b"))


def test_no_user_message_raises():
    with pytest.raises(OpenAIRequestError, match="user"):
        resolve_openai_request(
            _req(messages=[OpenAIMessage(role="system", content="x")])
        )
