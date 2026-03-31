"""Stable topic names for Phase 0 (single topic; tenant in envelope + headers)."""

CONTROLPLANE_EVENTS_V1 = "controlplane.events.v1"

# Phase 1.5 — event_type strings (payloads on CONTROLPLANE_EVENTS_V1 unless split later)
LISTING_CREATED_V1 = "listing.created.v1"
PURCHASE_COMPLETED_V1 = "purchase.completed.v1"
TOKENS_USED_V1 = "tokens.used.v1"
BUDGET_DEPLETED_V1 = "budget.depleted.v1"
