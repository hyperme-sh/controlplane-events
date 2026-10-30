from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class AuthTokenIssuedPayload(BaseModel):
    subject: str = Field(..., description="OIDC sub")
    grant_type: str | None = None
    client_id: str | None = None


class AuthLoginSuccessPayload(BaseModel):
    subject: str
    identity_provider: str | None = Field(
        default=None,
        description="Broker IdP alias when using Keycloak identity brokering (e.g. google, microsoft).",
    )
    tenant_id: uuid.UUID | None = None
