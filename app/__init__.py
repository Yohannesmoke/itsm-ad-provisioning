"""ITSM-to-Active Directory provisioning webhook."""

from __future__ import annotations

import hmac
import logging
import os
import time

from flask import Flask, jsonify, request

from .itsm_client import NullNotifier, ServiceDeskPlusNotifier, TicketNotifier, render_note
from .parser import ParseError, parse_ticket_html
from .provisioner import Provisioner, build_provisioner

logger = logging.getLogger("itsm_ad_provisioning")


def create_app(
    provisioner: Provisioner | None = None,
    notifier: TicketNotifier | None = None,
    webhook_token: str | None = None,
) -> Flask:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    token = webhook_token if webhook_token is not None else os.environ.get("WEBHOOK_TOKEN", "")
    if not token:
        raise RuntimeError("WEBHOOK_TOKEN must be set")

    provisioner = provisioner or build_provisioner(os.getenv("PROVISIONER", "dry-run"))
    if notifier is None:
        base_url = os.getenv("SDP_BASE_URL")
        notifier = (
            ServiceDeskPlusNotifier(base_url, os.environ["SDP_API_KEY"], verify_tls=os.getenv("SDP_VERIFY_TLS", "true") == "true")
            if base_url
            else NullNotifier()
        )

    app = Flask(__name__)
    started = time.time()

    def authorised() -> bool:
        supplied = request.headers.get("X-Webhook-Token", "")
        return hmac.compare_digest(supplied.encode(), token.encode())

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", uptime_seconds=round(time.time() - started))

    @app.post("/webhook/create-user")
    def create_user():
        if not authorised():
            logger.warning("Rejected webhook from %s: bad token", request.remote_addr)
            return jsonify(error="unauthorized"), 401

        payload = request.get_json(silent=True) or {}
        ticket_id = str(payload.get("request_id") or payload.get("id") or "")
        try:
            users, skipped = parse_ticket_html(payload.get("description", ""))
        except ParseError as exc:
            logger.info("Ticket %s: %s", ticket_id or "?", exc)
            return jsonify(error=str(exc)), 400

        results = []
        for user in users:
            res = provisioner.create(user)
            status = "ok" if res.success else "failed"
            logger.info("Ticket %s: %s -> %s (%s)", ticket_id or "?", user.username, status, res.message)
            results.append(res)

        if ticket_id:
            try:
                notifier.add_note(ticket_id, render_note(users, results, skipped))
            except Exception:  # write-back must never lose the provisioning result
                logger.exception("Could not write result back to ticket %s", ticket_id)

        succeeded = sum(r.success for r in results)
        # Passwords are deliberately NOT returned in the HTTP response.
        return jsonify(
            ticket_id=ticket_id or None,
            total=len(results) + len(skipped),
            succeeded=succeeded,
            failed=len(results) - succeeded + len(skipped),
            results=[{"username": r.username, "success": r.success, "message": r.message} for r in results]
            + [{"username": s.name, "success": False, "message": s.reason} for s in skipped],
        )

    return app
