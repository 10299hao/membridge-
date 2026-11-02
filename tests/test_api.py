from fastapi.testclient import TestClient

from membridge.api import create_app
from membridge.engine import Engine


def test_gateway_import_worker_search_handoff_and_auth(settings, export):
    settings.api_key = "test-token"
    app = create_app(settings=settings)
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.post("/imports", json={"project": "a", "data": export}).status_code == 401
        response = client.post("/imports", json={"project": "a", "data": export}, headers=headers)
        assert response.status_code == 202
        job = response.json()
        engine = Engine(settings)
        assert engine.process_one(job["id"])
        assert (
            client.get(f"/jobs/{job['id']}", params={"project": "a"}, headers=headers).json()["state"]
            == "complete"
        )
        assert client.get(f"/jobs/{job['id']}", params={"project": "b"}, headers=headers).status_code == 404
        result = client.post("/search", json={"project": "a", "query": "pagination"}, headers=headers)
        memory = result.json()["memories"][0]
        evidence = client.get(f"/memories/{memory['id']}/evidence", params={"project": "a"}, headers=headers)
        assert evidence.status_code == 200
        assert evidence.json()["messages"][0]["id"] == "msg_todo"
        handoff = client.post("/handoff", json={"project": "a"}, headers=headers)
        assert "Postgres" in handoff.json()["brief"]
        assert client.post("/handoff", json={"project": "b"}, headers=headers).status_code == 404


def test_api_validation_does_not_echo_transcript_secrets(settings):
    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/imports", json={"project": "a", "source_tool": "SECRET_VALUE", "data": {}})
        assert response.status_code == 422
        assert "SECRET_VALUE" not in response.text
        response = client.post("/imports", json={"project": "a", "data": {"messages": []}})
        assert response.status_code == 400
        response = client.post("/imports", content=b"x" * (8 * 1024 * 1024 + 1))
        assert response.status_code == 413


def test_ingestion_and_ai_service_contracts(settings, export):
    with TestClient(create_app("ingestion", settings)) as client:
        response = client.post("/normalize", json={"project": "a", "data": export})
        assert response.status_code == 200
        session = response.json()
    with TestClient(create_app("ai", settings)) as client:
        response = client.post("/extract", json=session)
        assert response.status_code == 200
        assert any(m["type"] == "decision" for m in response.json()["memories"])
        response = client.post("/embed", json={"texts": ["Postgres"]})
        assert response.status_code == 200
        assert response.json() == {"model": "lexical", "vectors": [[]]}
