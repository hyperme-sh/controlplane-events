from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
import errno
import json
import socket
import time
import uuid
import pytest
from controlplane_events.envelope import ControlPlaneEventEnvelope
from controlplane_events.http_relay import (
    HttpEventSender, TransientDeliveryError, _retry_delay, _temporary_transport_failure, run_relay)


@pytest.mark.parametrize("url", ["file:///tmp/outbox", "https://user:pass@host/", "https://host/?token=x",
                                  "https://host/#x", "http://example.com/events", "http://192.0.2.10/events",
                                  "http://[2001:db8::10]/events"])
def test_destination_rejects_credentials_and_non_http(url):
    with pytest.raises(ValueError):
        HttpEventSender(url, "token")


@pytest.mark.parametrize("url", ["https://example.com/events", "http://127.0.0.1/events", "http://localhost/events",
                                  "http://LOCALHOST/events", "http://[::1]/events", "http://127.255.255.254/events"])
def test_https_and_loopback_http_destinations(url):
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
        elif status >= 500:
            with pytest.raises(TransientDeliveryError):
                sender(event_id, envelope)
        else:
            with pytest.raises(Exception) as caught:
                sender(event_id, envelope)
            assert not isinstance(caught.value, TransientDeliveryError)
        assert len(received) == 1
        headers, body = received[0]
        assert headers['Authorization'] == 'Bearer producer-token'
        assert headers['Idempotency-Key'] == str(event_id)
        assert body == {'event_id': str(event_id), 'envelope': envelope.model_dump(mode='json')}
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _envelope():
    tenant = uuid.uuid4()
    return ControlPlaneEventEnvelope(event_type="payment.obligation.recorded.v1", service="payment-service",
        tenant_id=tenant, payload={"tenant_id":str(tenant), "lease_id":str(uuid.uuid4()),
        "billing_record_id":str(uuid.uuid4()), "source_event_id":str(uuid.uuid4()), "net_usdc_atoms":10})


def test_connection_closed_before_response_is_transient():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        listener.settimeout(2)
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(2)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data += chunk
            except OSError:
                return

    thread = Thread(target=serve, daemon=True)
    thread.start()
    sender = HttpEventSender(f"http://127.0.0.1:{port}/events", "token", timeout=1)
    try:
        with pytest.raises(TransientDeliveryError):
            sender(uuid.uuid4(), _envelope())
    finally:
        listener.close()
        thread.join(2)


def test_reset_without_a_response_is_classified_transient():
    import http.client
    from urllib.error import URLError
    assert _temporary_transport_failure(http.client.RemoteDisconnected())
    assert _temporary_transport_failure(ConnectionResetError(errno.ECONNRESET, "reset"))
    assert _temporary_transport_failure(OSError(errno.ECONNRESET, "reset"))
    assert _temporary_transport_failure(URLError(http.client.RemoteDisconnected()))


def test_loopback_http_does_not_follow_an_implicit_proxy(monkeypatch):
    seen = []

    class Proxy(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    closed = socket.socket()
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()
    proxy_url = f"http://127.0.0.1:{proxy.server_port}"
    monkeypatch.setenv("http_proxy", proxy_url)
    monkeypatch.setenv("HTTP_PROXY", proxy_url)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    sender = HttpEventSender(f"http://127.0.0.1:{port}/events", "producer-token", timeout=1)
    try:
        with pytest.raises(TransientDeliveryError):
            sender(uuid.uuid4(), _envelope())
        assert seen == []
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join()


def test_refused_connection_is_a_transient_delivery_failure():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    sender = HttpEventSender(f"http://127.0.0.1:{port}/events", "token", timeout=1)
    with pytest.raises(TransientDeliveryError):
        sender(uuid.uuid4(), _envelope())


def test_trickled_response_headers_honor_the_delivery_deadline():
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    stop = Event()

    def serve():
        listener.settimeout(2)
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(2)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    data += chunk
                payload = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                for byte in payload:
                    if stop.is_set():
                        return
                    time.sleep(0.025)
                    conn.sendall(bytes([byte]))
            except OSError:
                return
    thread = Thread(target=serve, daemon=True)
    thread.start()
    sender = HttpEventSender(f"http://127.0.0.1:{port}/events", "producer-token", timeout=0.05)
    started = time.monotonic()
    try:
        with pytest.raises(TransientDeliveryError):
            sender(uuid.uuid4(), _envelope())
        assert time.monotonic() - started < 1.0
    finally:
        stop.set()
        listener.close()
        thread.join(2)


def test_transient_delivery_retries_with_backoff_and_keeps_other_tenants_moving():
    clock = {"now": 0.0}
    sleeps = []
    calls = []

    def monotonic():
        return clock["now"]

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += seconds

    def relay(engine, tenant, sender):
        calls.append((tenant, clock["now"]))
        if tenant == "down" and sum(item[0] == "down" for item in calls) < 3:
            raise TransientDeliveryError("receiver refused")

    run_relay(object(), object(), ["down", "up"], once=False, sleep=sleep, monotonic=monotonic,
              relay=relay, rounds=4)
    assert [item for item in calls if item[0] == "down"] == [("down", 0.0), ("down", 1.0), ("down", 3.0)]
    assert [item for item in calls if item[0] == "up"] == [("up", 0.0), ("up", 1.0), ("up", 2.0), ("up", 3.0)]
    assert sleeps == [1, 1, 1]


def test_retry_delay_stays_capped_across_a_long_outage():
    assert _retry_delay(1) == 1
    assert _retry_delay(2) == 2
    assert _retry_delay(6) == 32
    assert _retry_delay(7) == 60
    assert _retry_delay(1025) == 60
    assert _retry_delay(10**6) == 60


def test_one_shot_and_permanent_delivery_failures_propagate():
    def refuse(engine, tenant, sender):
        raise TransientDeliveryError("timed out")

    with pytest.raises(TransientDeliveryError):
        run_relay(object(), object(), ["only"], once=True, relay=refuse, rounds=3)

    def permanent(engine, tenant, sender):
        raise RuntimeError("receiver rejected the event")

    with pytest.raises(RuntimeError):
        run_relay(object(), object(), ["only", "other"], once=False, relay=permanent, rounds=3)


def test_fanout_retries_same_facts_after_partial_receiver_commit():
    from controlplane_events.http_relay import HttpEventFanout
    states=[{'token':'first-fixture','fail':False,'requests':[],'receipts':{},'effects':0},
            {'token':'second-fixture','fail':True,'requests':[],'receipts':{},'effects':0}]
    servers=[];threads=[]
    def handler(state):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw=self.rfile.read(int(self.headers['Content-Length']))
                if self.headers.get('Authorization')!='Bearer '+state['token']:
                    self.send_response(401);self.end_headers();return
                body=json.loads(raw);identity=self.headers['Idempotency-Key']
                state['requests'].append((identity,raw))
                if state['fail']:
                    self.send_response(503);self.end_headers();return
                if identity in state['receipts']:
                    assert state['receipts'][identity]==raw
                else:
                    state['receipts'][identity]=raw;state['effects']+=1
                self.send_response(200);self.end_headers()
            def log_message(self,*args):pass
        return Handler
    try:
        for state in states:
            server=ThreadingHTTPServer(('127.0.0.1',0),handler(state));servers.append(server)
            thread=Thread(target=server.serve_forever,daemon=True);thread.start();threads.append(thread)
        send=HttpEventFanout(HttpEventSender(f'http://127.0.0.1:{server.server_port}/events',state['token'])
                             for server,state in zip(servers,states))
        tenant,eid=uuid.uuid4(),uuid.uuid4()
        fact=ControlPlaneEventEnvelope(event_type='payment.obligation.recorded.v1',service='payment-service',
            tenant_id=tenant,payload={'tenant_id':str(tenant),'lease_id':str(uuid.uuid4()),
            'billing_record_id':str(uuid.uuid4()),'source_event_id':str(uuid.uuid4()),'net_usdc_atoms':10})
        with pytest.raises(TransientDeliveryError):send(eid,fact)
        assert [state['effects'] for state in states]==[1,0]
        states[1]['fail']=False;send(eid,fact)
        assert [state['effects'] for state in states]==[1,1]
        assert states[0]['requests']==states[1]['requests']
        assert states[0]['requests'][0]==states[0]['requests'][1]
    finally:
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join(timeout=2)


@pytest.mark.parametrize('count',[0,9])
def test_fanout_destination_count_is_bounded(count):
    from controlplane_events.http_relay import HttpEventFanout
    with pytest.raises(ValueError):HttpEventFanout([lambda *args:None]*count)


@pytest.mark.parametrize('arguments',[
    ['--destination','https://first.invalid/events','--destination','https://second.invalid/events','--token-file','/unreadable'],
    ['--destination','https://same.invalid/events','--destination','https://same.invalid/events','--token-file','/unreadable','--token-file','/unreadable']])
def test_cli_rejects_ambiguous_fanout_before_reading_credentials(monkeypatch,arguments):
    from controlplane_events.http_relay import main
    monkeypatch.setattr('sys.argv',['relay','--database-url-file','/unreadable',*arguments])
    with pytest.raises(SystemExit) as error:main()
    assert error.value.code==2
