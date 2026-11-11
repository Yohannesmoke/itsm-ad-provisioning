from app import create_app
from app.provisioner import DryRunProvisioner


def post(client, body, token="test-token"):
    return client.post("/webhook/create-user", json=body, headers={"X-Webhook-Token": token})


def test_health(client):
    assert client.get("/healthz").get_json()["status"] == "ok"


def test_rejects_bad_token(client, sample_ticket):
    assert post(client, sample_ticket, token="wrong").status_code == 401
    assert client.post("/webhook/create-user", json=sample_ticket).status_code == 401


def test_provisions_and_reports(client, sample_ticket, notifier):
    resp = post(client, sample_ticket)
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["total"] == 3 and data["succeeded"] == 2 and data["failed"] == 1
    assert "password" not in resp.get_data(as_text=True).lower()  # never returned over HTTP

    ticket_id, note = notifier.notes[0]
    assert ticket_id == "10423"
    assert "abebe.kebede" in note and "Temporary password" in note


def test_second_run_reports_existing_users(client, sample_ticket):
    post(client, sample_ticket)
    data = post(client, sample_ticket).get_json()
    assert data["succeeded"] == 0
    assert all("already exists" in r["message"] or "Missing" in r["message"] for r in data["results"])


def test_bad_ticket_returns_400(client):
    resp = post(client, {"request_id": "1", "description": "<p>hello</p>"})
    assert resp.status_code == 400 and "No table" in resp.get_json()["error"]


def test_notifier_failure_does_not_break_response(sample_ticket):
    class Broken:
        def add_note(self, *_):
            raise ConnectionError("ITSM down")

    app = create_app(provisioner=DryRunProvisioner(), notifier=Broken(), webhook_token="t")
    resp = app.test_client().post("/webhook/create-user", json=sample_ticket, headers={"X-Webhook-Token": "t"})
    assert resp.status_code == 200 and resp.get_json()["succeeded"] == 2
