from fastapi.testclient import TestClient

from baseball_backend.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database_configured"] is True
    assert body["ml_package_version"] is not None
