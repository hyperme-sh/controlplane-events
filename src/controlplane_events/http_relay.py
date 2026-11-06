"""Bounded HTTP delivery for a service-owned outbox.

The destination authenticates the token as one producer, validates the envelope,
and commits its inbox/domain transaction before returning a successful response.
Never put credentials in a URL; redirects are refused to avoid forwarding them.
"""
from __future__ import annotations

import json
import math
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpEventSender:
    def __init__(self, url: str, token: str, *, timeout: float = 5.0):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("an HTTP event destination is required")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("event URL must not contain credentials, query or fragment")
        if not token or token != token.strip() or any(ord(c) < 32 or ord(c) > 126 for c in token):
            raise ValueError("a nonempty single-line producer token is required")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("HTTP timeout must be between zero and 60 seconds")
        self.url = url
        self.token = token
        self.timeout = timeout
        self.opener = build_opener(_NoRedirect())

    def __call__(self, event_id, envelope):
        from controlplane_events.contracts import validate_event_envelope
        validate_event_envelope(envelope)
        body = json.dumps({"event_id": str(event_id), "envelope": envelope.model_dump(mode="json")},
                          separators=(",", ":")).encode()
        request = Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.token,
            "Idempotency-Key": str(event_id),
        })
        with self.opener.open(request, timeout=self.timeout) as response:
            # Async acceptance (202) is not proof of a committed receiver inbox.
            if response.status not in (200, 201, 204):
                raise RuntimeError("event receiver did not acknowledge a committed event")


def main():
    """One process per owning schema; compose supplies secrets as files."""
    import argparse
    from pathlib import Path
    import time
    import uuid
    from sqlalchemy import create_engine, text
    from controlplane_events.outbox import relay_batch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-file', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--tenant', type=uuid.UUID, action='append')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    sender = HttpEventSender(args.destination, args.token_file.read_text().strip())
    engine = create_engine(args.database_url_file.read_text().strip(), pool_pre_ping=True)
    try:
        while True:
            tenants = args.tenant
            if tenants is None:
                with engine.connect() as db:
                    tenants = db.scalars(text('SELECT tenant_id FROM service_tenants ORDER BY tenant_id')).all()
            for tenant in tenants:
                relay_batch(engine, tenant, sender)
            if args.once:
                return
            time.sleep(1)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
