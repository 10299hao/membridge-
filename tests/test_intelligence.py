import json

import httpx
import pytest

from membridge.adapters import parse
from membridge.intelligence import Intelligence


def test_compatible_provider_extraction_embeddings_and_invalid_evidence(settings, export, monkeypatch):
    settings.llm_url = "http://provider.test/v1"
    settings.llm_model = "test-chat"
    settings.embedding_model = "test-embeddings"
    settings.llm_key = "fake-secret"
    bad_evidence = False
    calls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer fake-secret"
        body = json.loads(request.content)
        calls.append(request.url.path)
        if request.url.path.endswith("chat/completions"):
            assert body["model"] == "test-chat"
            assert body["messages"][0]["role"] == "system"
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "memories": [
                                            {
                                                "type": "decision",
                                                "text": "Chose Postgres",
                                                "evidence": ["invented" if bad_evidence else "msg_decision"],
                                                "status": "active",
                                            }
                                        ]
                                    }
                                )
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={"data": [{"index": i, "embedding": [1.0, 0.5, 0.2]} for i in range(len(body["input"]))]},
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )
    intelligence = Intelligence(settings)
    assert intelligence.mode == "llm"
    memories = intelligence.extract(parse(export, "opencode", "a"))
    assert memories[0].text == "Chose Postgres"
    model, vectors = intelligence.embed(["Postgres", "transactions"])
    assert model.endswith("test-embeddings") and len(vectors) == 2
    assert calls == ["/v1/chat/completions", "/v1/embeddings"]
    bad_evidence = True
    with pytest.raises(ValueError, match="evidence"):
        intelligence.extract(parse(export, "opencode", "a"))


def test_extract_only_mode_does_not_claim_semantic_search(settings, export):
    intelligence = Intelligence(settings)
    assert intelligence.mode == "extractive"
    assert intelligence.embed(["hello"]) == ("lexical", [[]])
    memories = intelligence.extract(parse(export, "opencode", "a"))
    assert any(m.type == "todo" and "pagination" in m.text for m in memories)
    assert all(m.evidence for m in memories)


def test_long_messages_are_chunked_instead_of_truncated(settings, export, monkeypatch):
    settings.llm_url, settings.llm_model = "http://provider/v1", "test"
    session = parse(export, "opencode", "a")
    session.messages = [session.messages[0]]
    session.messages[0].content = "x" * 45000 + "LAST_SENTENCE"
    chunks = []

    def post(path, body):
        chunks.append(body["messages"][1]["content"])
        return {"choices": [{"message": {"content": '{"memories":[]}'}}]}

    intelligence = Intelligence(settings)
    monkeypatch.setattr(intelligence, "_post", post)
    intelligence.extract(session)
    assert len(chunks) > 1 and "LAST_SENTENCE" in chunks[-1]
