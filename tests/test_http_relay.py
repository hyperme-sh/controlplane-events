from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import json
import uuid
import pytest
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.http_relay import HttpEventSender


@pytest.mark.parametrize("url", ["file:///tmp/outbox", "https://user:pass@host/", "https://host/?token=x", "https://host/#x"])
def test_destination_rejects_credentials_and_non_http(url):
    with pytest.raises(ValueError):
        HttpEventSender(url, "token")


@pytest.mark.parametrize("token", ["", " token", "x\ny", "x\ry"])
def test_producer_credential_required(token):
    with pytest.raises(ValueError):
        HttpEventSender("http://localhost/events", token)


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf"), True])
def test_bounded_timeout(timeout):
    with pytest.raises(ValueError):
        HttpEventSender("http://localhost/events", "token", timeout=timeout)


@pytest.mark.parametrize("status", [200, 201, 204, 202, 302, 500])
def test_http_commit_acknowledgement(status):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            self.send_response(status)
            if status == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        event_id = uuid.uuid4()
        tenant = uuid.uuid4()
        envelope = ControlPlaneEventEnvelope(event_type="payment.obligation.recorded.v1", service="payment-service",
            tenant_id=tenant, payload={"tenant_id":str(tenant), "lease_id":str(uuid.uuid4()),
            "billing_record_id":str(uuid.uuid4()), "source_event_id":str(uuid.uuid4()), "net_usdc_atoms":10})
        sender = HttpEventSender(f"http://127.0.0.1:{server.server_port}/events", "producer-token")
        if status in (200, 201, 204):
            sender(event_id, envelope)
        else:
            with pytest.raises(Exception):
                sender(event_id, envelope)
        assert len(received) == 1
        headers, body = received[0]
        assert headers['Authorization'] == 'Bearer producer-token'
        assert headers['Idempotency-Key'] == str(event_id)
        assert body == {'event_id': str(event_id), 'envelope': envelope.model_dump(mode='json')}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
