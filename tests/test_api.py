from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_health_check():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "catio-api"}


def test_invalid_telegram_auth():
    response = client.post("/api/v1/auth/telegram", json={"init_data": "invalid_data"})
    assert response.status_code == 400
