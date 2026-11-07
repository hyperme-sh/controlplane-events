# Events owner runtime

Build from the exact qualified wheel with `prepare-runtime.py --wheel PATH
--output FRESH_CONTEXT`, then build the root Dockerfile using that context and
the recorded source SHA. The context contains only the wheel, frozen public
HTTP dependencies, owner migration resources and entrypoint. No private fetches,
credentials or broad source copies enter Docker. Dirty prototypes are explicitly
unqualified. No registry write token or publication is part of runtime CI.

Provision a dedicated nonprivileged role and nonpublic owner schema separately,
using the packaged `python -m controlplane_events.migrate --database-url-file
PATH`. Normal startup only reads the schema inventory and immutable migration
receipt. It refuses public schemas, privileged roles and incomplete or divergent
receipts. Supply DATABASE_URL_FILE and EVENT_PRODUCER_TOKENS_FILE as readable
private mounts. The default process serves the authenticated HTTP inbox as UID
65532. Terminate HTTPS at the existing trusted edge; relays require HTTPS for
nonloopback destinations.

EVENTS_LEGACY_LOG_FORWARD defaults to 0. Setting exactly 1 enables the fixed
compatibility append to public.controlplane_events on the inbox transaction.
Grant only INSERT on that existing log and USAGE on its sequence; payload reads,
consumer cursor changes and business-owner grants are unnecessary. The startup
guard checks append authority when enabled. Existing legacy log rows, integer IDs
and consumer cursors remain intact. UUID receipts stay in the owner inbox. Lost
acknowledgements retry the same receipt without a second append. Append failure
rolls back the receipt. This is a temporary transport bridge; it neither moves
business ownership nor changes event facts.

`qualify-runtime.py IMAGE --wheel PATH` provisions disposable internal-only
PostgreSQL, replays the actual owner migration, runs the real default process and
checks a real HTTP delivery/retry produces one legacy append. It checks image
wheel/source provenance and refusal of privileged roles, public schemas, missing
receipts and checksum drift, then removes containers, network and test credentials.
No production credential, external provider or live event log is involved.
