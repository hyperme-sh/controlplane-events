from controlplane_events.http_consumer import producer_owns_event


def test_authenticated_producer_cannot_submit_another_owners_event():
    assert producer_owns_event("hypermesh-plane", "hypermesh.lease.outcome.v1")
    assert producer_owns_event("payment-service", "payment.obligation.recorded.v1")
    assert not producer_owns_event("payment-service", "hypermesh.lease.outcome.v1")
    assert not producer_owns_event("hypermesh-plane", "payment.obligation.recorded.v1")
    assert not producer_owns_event("payment-service", "not.a.registered.event")
    assert not producer_owns_event("", "payment.obligation.recorded.v1")
