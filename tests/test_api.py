from fastapi.testclient import TestClient

from public_agent.api.app import create_app


class HealthyDatabase:
    def __init__(self) -> None:
        self.disposed = False

    async def ping(self) -> None:
        return None

    async def dispose(self) -> None:
        self.disposed = True


class BrokenDatabase(HealthyDatabase):
    async def ping(self) -> None:
        raise ConnectionError("database unavailable")


def test_health_endpoints() -> None:
    with TestClient(create_app(database=HealthyDatabase())) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_readiness_returns_503_when_database_is_unavailable() -> None:
    with TestClient(create_app(database=BrokenDatabase())) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "reason": "ConnectionError"}
