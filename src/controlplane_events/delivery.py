"""Wire size of one HTTP event delivery. Receivers reject anything larger."""
import json

MAX_DELIVERY_BYTES = 256 * 1024


def delivery_body(event_id, envelope) -> bytes:
    """Exact bytes HttpEventSender posts and the receivers measure."""
    return json.dumps(
        {"event_id": str(event_id), "envelope": envelope.model_dump(mode="json")},
        separators=(",", ":"),
    ).encode()
