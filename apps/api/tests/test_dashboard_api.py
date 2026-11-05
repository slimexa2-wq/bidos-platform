from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_expiring_and_bonds_dashboard(monkeypatch):
    # Mock event store
    class MockEvent:
        def __init__(self, id, type, title, detail, severity="info"):
            self.id = id
            self.type = type
            self.title = title
            self.detail = detail
            self.severity = severity
            self.project_id = 'test-id'
            self.resolution_hint = ''
            self.resolution_key = ''
            self.occurred_at = "2023-10-01T00:00:00Z"
            self.due_at = None
            self.handled = False
            self.persistent = False
            self.metadata = {}
            self.source_pointer_ids = []

    class MockEventStore:
        def list_open(self, type_prefixes=None):
            from app.domain import EventType
            return [
                MockEvent("1", EventType.PROJECT_EXPIRING, "Project A Expiring", "Detail A"),
                MockEvent("2", EventType.DEPOSIT_REFUND_DUE, "Bond B Due", "Detail B"),
            ]

    import app.main as main_module
    monkeypatch.setattr(main_module, "event_store", MockEventStore())

    response = client.get("/api/dashboard/expiring-and-bonds")
    assert response.status_code == 200
    data = response.json()
    assert data["totalCount"] == 2
    assert len(data["expiringProjects"]) == 1
    assert len(data["bidBonds"]) == 1
