from fastapi.testclient import TestClient

from media_club.app import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_unconfigured_dependencies() -> None:
    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {
        "database": "not_configured",
        "discord": "not_configured",
    }
