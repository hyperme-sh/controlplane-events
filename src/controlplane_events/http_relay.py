"""Bounded HTTP delivery for a service-owned outbox.

The destination authenticates the token as one producer, validates the envelope,
and commits its inbox/domain transaction before returning a successful response.
Never put credentials in a URL; redirects are refused to avoid forwarding them.
Cleartext HTTP is limited to loopback hosts so a bearer token is not sent to a
remote network peer. Implicit environment proxies are disabled, so a loopback
request is not redirected to ``http_proxy``. The long-running process retries
temporary receiver failures with bounded backoff instead of exiting.
"""
from __future__ import annotations

import errno
import http.client
import io
import ipaddress
import logging
import math
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

logger = logging.getLogger(__name__)


class TransientDeliveryError(Exception):
    """The receiver refused, timed out, or returned 5xx. The outbox batch rolls back."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeadlineSocket:
    """Bound every socket read and write to one absolute deadline.

    urllib's timeout is applied to each blocking call. A peer that returns a
    byte before that per-call timer fires can hold the worker, and the outbox
    row locks, indefinitely.
    """

    def __init__(self, sock, deadline_at):
        self._sock = sock
        self._deadline_at = deadline_at
        self._io_refs = 0
        self._closed = False

    def _arm(self):
        sock = self._sock
        if sock is None:
            raise TimeoutError("event delivery exceeded the deadline")
        remaining = self._deadline_at - time.monotonic()
        if remaining <= 0:
            self._close_raw()
            raise TimeoutError("event delivery exceeded the deadline")
        sock.settimeout(remaining)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self._arm()
        try:
            return self._sock.recv_into(buffer, nbytes, flags)
        except TimeoutError as exc:
            self._close_raw()
            raise TimeoutError("event delivery exceeded the deadline") from exc

    def sendall(self, data, flags=0):
        self._arm()
        try:
            return self._sock.sendall(data, flags)
        except TimeoutError as exc:
            self._close_raw()
            raise TimeoutError("event delivery exceeded the deadline") from exc

    def makefile(self, mode="r", buffering=None, *, encoding=None, errors=None, newline=None):
        self._io_refs += 1
        raw = socket.SocketIO(self, "rb" if "b" in mode else "r")
        if buffering is None or buffering < 0:
            buffering = io.DEFAULT_BUFFER_SIZE
        return io.BufferedReader(raw, buffering)

    def _decref_socketios(self):
        if self._io_refs > 0:
            self._io_refs -= 1
        if self._closed and self._io_refs <= 0:
            self._close_raw()

    def close(self):
        self._closed = True
        if self._io_refs <= 0:
            self._close_raw()

    def _close_raw(self):
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def __getattr__(self, name):
        sock = self._sock
        if sock is None:
            raise OSError("event delivery socket is closed")
        return getattr(sock, name)


class _DeadlineHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port=None, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 source_address=None, blocksize=8192, *, deadline_at):
        super().__init__(host, port, timeout, source_address, blocksize)
        self._deadline_at = deadline_at

    def connect(self):
        remaining = self._deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("event delivery exceeded the deadline")
        self.timeout = remaining
        super().connect()
        self.sock = _DeadlineSocket(self.sock, self._deadline_at)


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, port=None, *, timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
                 source_address=None, context=None, blocksize=8192, deadline_at):
        super().__init__(host, port, timeout=timeout, source_address=source_address,
                         context=context, blocksize=blocksize)
        self._deadline_at = deadline_at

    def connect(self):
        remaining = self._deadline_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("event delivery exceeded the deadline")
        self.timeout = remaining
        super().connect()
        self.sock = _DeadlineSocket(self.sock, self._deadline_at)


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline_at):
        super().__init__()
        self._deadline_at = deadline_at

    def http_open(self, req):
        return self.do_open(_DeadlineHTTPConnection, req, deadline_at=self._deadline_at)


class _DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(self, deadline_at):
        super().__init__()
        self._deadline_at = deadline_at

    def https_open(self, req):
        return self.do_open(_DeadlineHTTPSConnection, req, deadline_at=self._deadline_at)


def _loopback_http_host(hostname):
    host = hostname.strip().rstrip(".").lower()
    if host == "localhost":
        return True
    try:
        return bool(ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def _temporary_transport_failure(exc):
    if isinstance(exc, HTTPError):
        return 500 <= exc.code <= 599
    reason = exc.reason if isinstance(exc, URLError) else exc
    # A peer that accepts and then closes, or resets, has not committed the event.
    if isinstance(reason, (TimeoutError, ConnectionRefusedError, ConnectionResetError)):
        return True
    return getattr(reason, "errno", None) in {errno.ECONNREFUSED, errno.ETIMEDOUT, errno.ECONNRESET}


class HttpEventSender:
    def __init__(self, url: str, token: str, *, timeout: float = 5.0):
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("an HTTP event destination is required")
        if parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise ValueError("event URL must not contain credentials, query or fragment")
        if parsed.scheme == "http" and not _loopback_http_host(parsed.hostname):
            raise ValueError("remote event destinations require HTTPS")
        if not token or token != token.strip() or any(ord(c) < 32 or ord(c) > 126 for c in token):
            raise ValueError("a nonempty single-line producer token is required")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("HTTP timeout must be between zero and 60 seconds")
        self.url = url
        self.token = token
        self.timeout = timeout

    def __call__(self, event_id, envelope):
        from controlplane_events.contracts import validate_event_envelope
        from controlplane_events.delivery import delivery_body
        validate_event_envelope(envelope)
        body = delivery_body(event_id, envelope)
        request = Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + self.token,
            "Idempotency-Key": str(event_id),
        })
        deadline_at = time.monotonic() + self.timeout
        # ProxyHandler({}) replaces urllib's env-proxy handler. Otherwise an
        # http_proxy receives the loopback bearer and its 200 marks the outbox delivered.
        opener = build_opener(ProxyHandler({}), _NoRedirect(), _DeadlineHTTPHandler(deadline_at),
                              _DeadlineHTTPSHandler(deadline_at))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                # Async acceptance (202) is not proof of a committed receiver inbox.
                if response.status not in (200, 201, 204):
                    raise RuntimeError("event receiver did not acknowledge a committed event")
        except Exception as exc:
            if _temporary_transport_failure(exc):
                raise TransientDeliveryError("temporary event delivery failure") from exc
            raise


def _retry_delay(attempt):
    """Bounded exponential backoff. Cap the shift before converting to float.

    ``float(2 ** 1024)`` raises OverflowError, which would escape the recovery
    loop on a long outage. Exponents at or above 6 are already the 60 second cap.
    """
    exponent = min(max(int(attempt), 1) - 1, 6)
    return min(60.0, float(2 ** exponent))


def run_relay(engine, sender, tenants, *, once, sleep=time.sleep, monotonic=time.monotonic,
              relay=None, rounds=None):
    """Deliver each tenant. Temporary receiver failures roll back and back off.

    ``rounds`` bounds the loop for tests. The process loop leaves it unset.
    Other tenants keep moving while one destination is backing off.
    """
    if relay is None:
        from controlplane_events.outbox import relay_batch
        relay = relay_batch
    failures = {}
    ready_at = {}
    completed = 0
    while rounds is None or completed < rounds:
        selected = tenants
        if selected is None:
            from sqlalchemy import text
            with engine.connect() as db:
                selected = db.scalars(text(
                    "SELECT tenant_id FROM service_tenants ORDER BY tenant_id")).all()
        now = monotonic()
        for tenant in selected:
            if ready_at.get(tenant, 0) > now:
                continue
            try:
                relay(engine, tenant, sender)
            except TransientDeliveryError as exc:
                if once:
                    raise
                attempt = failures.get(tenant, 0) + 1
                failures[tenant] = attempt
                delay = _retry_delay(attempt)
                ready_at[tenant] = monotonic() + delay
                logger.warning("temporary event delivery failure for %s; retrying in %.0fs (%s)",
                               tenant, delay, exc)
            else:
                failures.pop(tenant, None)
                ready_at.pop(tenant, None)
        completed += 1
        if once or (rounds is not None and completed >= rounds):
            return
        sleep(1)


def main():
    """One process per owning schema; compose supplies secrets as files."""
    import argparse
    from pathlib import Path
    import uuid
    from sqlalchemy import create_engine
    from controlplane_events.store import normalize_sync_url

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url-file', type=Path, required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--tenant', type=uuid.UUID, action='append')
    parser.add_argument('--once', action='store_true')
    filters = parser.add_mutually_exclusive_group()
    filters.add_argument('--event-type', action='append')
    filters.add_argument('--exclude-event-type', action='append')
    args = parser.parse_args()
    sender = HttpEventSender(args.destination, args.token_file.read_text().strip())
    engine = create_engine(normalize_sync_url(args.database_url_file.read_text()), pool_pre_ping=True)
    try:
        from functools import partial
        from controlplane_events.outbox import relay_batch
        relay = partial(relay_batch, event_types=args.event_type,
                        exclude_event_types=args.exclude_event_type)
        run_relay(engine, sender, args.tenant, once=args.once, relay=relay)
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
