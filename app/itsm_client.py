"""Write provisioning results back to the originating ticket.

Uses the ManageEngine ServiceDesk Plus REST API v3. Credentials are added as a
technician-only note (never shown to the requester) and are never logged.
"""

from __future__ import annotations

import html
import json
import logging
from typing import Protocol

import requests

from .parser import UserRequest
from .provisioner import ProvisionResult

logger = logging.getLogger(__name__)


class TicketNotifier(Protocol):
    def add_note(self, ticket_id: str, html_body: str) -> None: ...


class NullNotifier:
    """Used when no ITSM URL is configured (local development)."""

    def add_note(self, ticket_id: str, html_body: str) -> None:
        logger.info("ITSM write-back disabled; would add note to ticket %s", ticket_id)


class ServiceDeskPlusNotifier:
    def __init__(self, base_url: str, api_key: str, timeout: int = 15, verify_tls: bool = True) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"authtoken": api_key, "Accept": "application/vnd.manageengine.sdp.v3+json"})
        self.timeout = timeout
        self.verify_tls = verify_tls

    def add_note(self, ticket_id: str, html_body: str) -> None:
        payload = {"note": {"description": html_body, "show_to_requester": False, "mark_first_response": False}}
        resp = self.session.post(
            f"{self.base_url}/api/v3/requests/{ticket_id}/notes",
            data={"input_data": json.dumps(payload)},
            timeout=self.timeout,
            verify=self.verify_tls,
        )
        resp.raise_for_status()
        logger.info("Added provisioning note to ticket %s", ticket_id)


def render_note(users: list[UserRequest], results: list[ProvisionResult], skipped: list) -> str:
    """Build the HTML note. Temporary passwords appear only for accounts that were created."""
    by_name = {u.username: u for u in users}
    rows = []
    for r in results:
        pw = by_name[r.username].password if r.success and r.username in by_name else ""
        rows.append(
            "<tr>"
            f"<td>{html.escape(r.username)}</td>"
            f"<td>{'Created' if r.success else 'Failed'}</td>"
            f"<td>{html.escape(r.message)}</td>"
            f"<td>{html.escape(pw)}</td>"
            "</tr>"
        )
    for s in skipped:
        rows.append(f"<tr><td>{html.escape(s.name or '-')}</td><td>Skipped</td><td>{html.escape(s.reason)}</td><td></td></tr>")
    return (
        "<p><b>Automated AD provisioning result</b></p>"
        "<table border='1'><tr><th>Username</th><th>Status</th><th>Message</th><th>Temporary password</th></tr>"
        + "".join(rows)
        + "</table><p>Users must change the temporary password at first logon.</p>"
    )
