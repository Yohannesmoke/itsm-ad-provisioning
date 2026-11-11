import json
from pathlib import Path

import pytest

from app import create_app
from app.provisioner import DryRunProvisioner

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


class RecordingNotifier:
    def __init__(self):
        self.notes = []

    def add_note(self, ticket_id, html_body):
        self.notes.append((ticket_id, html_body))


@pytest.fixture
def sample_ticket():
    return json.loads((EXAMPLES / "sample_ticket.json").read_text())


@pytest.fixture
def notifier():
    return RecordingNotifier()


@pytest.fixture
def client(notifier):
    app = create_app(provisioner=DryRunProvisioner(), notifier=notifier, webhook_token="test-token")
    return app.test_client()
