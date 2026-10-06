from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import smtplib
import time as time_module
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID
from urllib.parse import quote

import asyncpg
import httpx
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "SchoolHop"
    database_url: str = "postgresql://schoolhop:schoolhop_dev_password@postgres:5432/schoolhop"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    cors_origins: str = "http://localhost:8088,http://127.0.0.1:8088"
    raw_location_retention_minutes: int = 120
    location_stale_seconds: int = 240
    safety_timeout_minutes: int = 180
    fcm_server_key: str | None = None
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_bundle_id: str | None = None
    apns_private_key: str | None = None
    apns_private_key_path: str | None = None
    apns_use_sandbox: bool = True
    google_maps_browser_key: str | None = None
    google_routes_api_key: str | None = None
    public_site_url: str = "https://schoolhop.shourish.com"
    environment: str = "production"
    expose_development_verification_codes: bool = False
    email_provider: str = "smtp"
    agentmail_api_key: str | None = None
    agentmail_from_email: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool = True
    slow_request_ms: int = 250

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
passwords = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
pool: asyncpg.Pool | None = None
logger = logging.getLogger("schoolhop")


def row_to_dict(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: serialize_value(row[key]) for key in row.keys()}


def serialize_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def rows_to_list(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [row_to_dict(row) or {} for row in rows]


async def db() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Database pool is not initialized")
    return pool


async def run_migrations(conn: asyncpg.Connection) -> None:
    await conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
    for path in sorted(Path("migrations").glob("*.sql")):
        version = path.stem
        applied = await conn.fetchval("SELECT 1 FROM schema_migrations WHERE version = $1", version)
        if applied:
            continue
        async with conn.transaction():
            await conn.execute(path.read_text())
            await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", version)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global pool
    pool = await asyncpg.create_pool(settings.database_url, min_size=1, max_size=10)
    async with pool.acquire() as conn:
        await run_migrations(conn)
    yield
    await pool.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def add_timing_headers(request: Request, call_next):
    started = time_module.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time_module.perf_counter() - started) * 1000
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if request.url.path.startswith("/api/") and elapsed_ms >= settings.slow_request_ms:
        logger.info("slow_request path=%s method=%s duration_ms=%.1f", request.url.path, request.method, elapsed_ms)
    return response


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=50)


class RegisterStartIn(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=50)


class RegisterVerifyIn(BaseModel):
    email: EmailStr
    code: str = Field(min_length=4, max_length=20)


class RegisterCompleteIn(BaseModel):
    email: EmailStr
    registration_token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class PasswordResetStartIn(BaseModel):
    email: EmailStr


class PasswordResetCompleteIn(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class SchoolIn(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    address: str | None = Field(default=None, max_length=240)
    city: str | None = Field(default=None, max_length=120)
    country: str = Field(default="DE", min_length=2, max_length=2)


class GroupIn(BaseModel):
    school_id: UUID
    name: str = Field(min_length=2, max_length=160)


class InviteIn(BaseModel):
    email: EmailStr


class JoinRequestIn(BaseModel):
    group_id: UUID
    note: str | None = Field(default=None, max_length=1000)


class JoinRequestDecisionIn(BaseModel):
    status: str = Field(pattern="^(approved|declined)$")
    note: str | None = Field(default=None, max_length=1000)


class GroupMemberIn(BaseModel):
    user_id: UUID


class ChildIn(BaseModel):
    school_id: UUID
    name: str = Field(min_length=1, max_length=120)
    year_group: str = Field(min_length=1, max_length=40)
    pickup_notes: str | None = Field(default=None, max_length=1000)
    home_address: str | None = Field(default=None, max_length=240)
    home_city: str | None = Field(default=None, max_length=120)
    home_country: str = Field(default="DE", min_length=2, max_length=2)
    home_latitude: float | None = Field(default=None, ge=-90, le=90)
    home_longitude: float | None = Field(default=None, ge=-180, le=180)
    home_place_id: str | None = Field(default=None, max_length=240)
    emergency_contact_name: str = Field(min_length=1, max_length=120)
    emergency_contact_phone: str = Field(min_length=1, max_length=80)


class DriverApprovalIn(BaseModel):
    driver_user_id: UUID
    approved: bool


class TripIn(BaseModel):
    driver_user_id: UUID
    service_date: date
    trip_type: str = Field(pattern="^(pickup|dropoff)$")
    expected_time: time
    child_ids: list[UUID] = Field(min_length=1)


class TripResponseIn(BaseModel):
    status: str = Field(pattern="^(accepted|declined)$")
    note: str | None = Field(default=None, max_length=500)


class ChangeRequestIn(BaseModel):
    request_type: str = Field(pattern="^(swap|change|absence)$")
    proposed_driver_user_id: UUID | None = None
    note: str = Field(min_length=1, max_length=1000)


class ChangeRequestDecisionIn(BaseModel):
    status: str = Field(pattern="^(accepted|declined)$")
    note: str | None = Field(default=None, max_length=1000)


class TripNoteIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)
    minutes: int | None = Field(default=None, ge=1, le=240)


class HandoverIn(BaseModel):
    handover_type: str = Field(pattern="^(picked_up|dropped_off|absent)$")
    note: str | None = Field(default=None, max_length=1000)


class LocationIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_meters: float | None = Field(default=None, ge=0, le=10000)
    speed_mps: float | None = Field(default=None, ge=0, le=200)
    heading_degrees: float | None = Field(default=None, ge=0, le=360)
    recorded_at: datetime | None = None


class MobileDeviceIn(BaseModel):
    provider: str = Field(pattern="^(apns|fcm)$")
    token: str = Field(min_length=10, max_length=4096)
    platform: str | None = Field(default=None, max_length=40)


class ClientErrorIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    source: str | None = Field(default=None, max_length=500)
    lineno: int | None = Field(default=None, ge=0)
    colno: int | None = Field(default=None, ge=0)
    stack: str | None = Field(default=None, max_length=4000)
    user_agent: str | None = Field(default=None, max_length=500)


def create_token(user_id: UUID) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"sub": str(user_id), "iat": int(now.timestamp()), "exp": int((now + timedelta(days=30)).timestamp())},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def normalize_email(email: str) -> str:
    return email.strip().lower()


def verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def verification_hash(email: str, code: str, purpose: str) -> str:
    payload = f"{purpose}:{normalize_email(email)}:{code.strip()}:{settings.jwt_secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reset_token_hash(token: str) -> str:
    payload = f"password_reset:{token}:{settings.jwt_secret}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return email
    masked_local = f"{local[:1]}***{local[-1:]}" if len(local) > 2 else f"{local[:1]}***"
    return f"{masked_local}@{domain}"


def can_expose_development_codes() -> bool:
    return settings.expose_development_verification_codes and settings.environment.lower() in {"development", "test", "testing", "local"}


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> str:
    provider = (settings.email_provider or "smtp").strip().lower()
    masked_to = mask_email(to)
    if provider == "agentmail":
        if not settings.agentmail_api_key or not settings.agentmail_from_email:
            logger.warning("Email delivery not configured for provider=agentmail recipient=%s subject=%s", masked_to, subject)
            return "not_configured"
        inbox_id = quote(settings.agentmail_from_email, safe="")
        payload = {"to": to, "subject": subject, "text": text}
        if html:
            payload["html"] = html
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"https://api.agentmail.to/v0/inboxes/{inbox_id}/messages/send",
                    headers={"Authorization": f"Bearer {settings.agentmail_api_key}"},
                    json=payload,
                )
            if response.status_code >= 400:
                logger.warning(
                    "Email delivery failed provider=agentmail recipient=%s subject=%s status_code=%s response=%s",
                    masked_to,
                    subject,
                    response.status_code,
                    response.text[:500],
                )
                return "failed"
        except httpx.HTTPError as exc:
            logger.warning("Email delivery error provider=agentmail recipient=%s subject=%s error=%s", masked_to, subject, exc)
            return "failed"
        return "sent"

    if not settings.smtp_host or not settings.smtp_from_email:
        logger.warning("Email delivery not configured for provider=smtp recipient=%s subject=%s", masked_to, subject)
        return "not_configured"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from_email
    message["To"] = to
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except OSError as exc:
        logger.warning("Email delivery error provider=smtp recipient=%s subject=%s error=%s", masked_to, subject, exc)
        return "failed"
    return "sent"


def delivery_payload(email: str, delivery_status: str, code: str, token: str | None = None) -> dict[str, Any]:
    expose_code = can_expose_development_codes()
    if delivery_status != "sent" and not expose_code:
        raise HTTPException(status_code=503, detail="Verification email could not be sent. Please try again later.")
    return {
        "email": email,
        "masked_email": mask_email(email),
        "delivery_status": delivery_status,
        "verification_code": code if expose_code and delivery_status != "sent" else None,
        "registration_token": token if expose_code and token else None,
        "expires_in_seconds": 15 * 60,
        "resend_after_seconds": 60,
    }


async def current_user(authorization: str | None = Header(default=None), database: asyncpg.Pool = Depends(db)) -> dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    user = await database.fetchrow("SELECT * FROM users WHERE id = $1 AND status = 'active'", UUID(payload["sub"]))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return row_to_dict(user) or {}


UserDep = Annotated[dict[str, Any], Depends(current_user)]
DbDep = Annotated[asyncpg.Pool, Depends(db)]


async def audit(database: asyncpg.Pool, actor: UUID | str | None, action: str, group_id: UUID | str | None = None, trip_id: UUID | str | None = None, details: dict[str, Any] | None = None) -> None:
    await database.execute(
        """
        INSERT INTO audit_records (actor_user_id, group_id, trip_id, action, details)
        VALUES ($1, $2, $3, $4, $5::jsonb)
        """,
        UUID(str(actor)) if actor else None,
        UUID(str(group_id)) if group_id else None,
        UUID(str(trip_id)) if trip_id else None,
        action,
        json.dumps(details or {}),
    )


def apns_private_key() -> str | None:
    if settings.apns_private_key:
        return settings.apns_private_key.replace("\\n", "\n")
    if settings.apns_private_key_path:
        path = Path(settings.apns_private_key_path)
        if path.exists():
            return path.read_text()
    return None


async def send_push_to_device(device: asyncpg.Record, title: str, body: str, data: dict[str, str]) -> None:
    if device["provider"] == "fcm":
        if not settings.fcm_server_key:
            return
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                "https://fcm.googleapis.com/fcm/send",
                headers={
                    "Authorization": f"key={settings.fcm_server_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": device["token"],
                    "notification": {"title": title, "body": body},
                    "data": data,
                    "priority": "high",
                },
            )
            response.raise_for_status()
        return

    private_key = apns_private_key()
    if not all([settings.apns_key_id, settings.apns_team_id, settings.apns_bundle_id, private_key]):
        return
    token = jwt.encode(
        {"iss": settings.apns_team_id, "iat": int(time_module.time())},
        private_key,
        algorithm="ES256",
        headers={"kid": settings.apns_key_id},
    )
    host = "api.sandbox.push.apple.com" if settings.apns_use_sandbox else "api.push.apple.com"
    async with httpx.AsyncClient(http2=True, timeout=8) as client:
        response = await client.post(
            f"https://{host}/3/device/{device['token']}",
            headers={
                "authorization": f"bearer {token}",
                "apns-topic": settings.apns_bundle_id,
                "apns-push-type": "alert",
                "apns-priority": "10",
            },
            json={
                "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
                **data,
            },
        )
        response.raise_for_status()


async def deliver_push(
    database: asyncpg.Pool,
    user_id: UUID | str,
    kind: str,
    title: str,
    body: str,
    entity_type: str | None,
    entity_id: UUID | str | None,
    action_url: str | None = None,
) -> None:
    devices = await database.fetch(
        "SELECT * FROM mobile_devices WHERE user_id = $1 AND enabled = true",
        UUID(str(user_id)),
    )
    data = {
        "kind": kind,
        "entity_type": entity_type or "",
        "entity_id": str(entity_id) if entity_id else "",
        "action_url": action_url or "",
    }
    for device in devices:
        try:
            await send_push_to_device(device, title, body, data)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {400, 403, 404, 410}:
                await database.execute("UPDATE mobile_devices SET enabled = false WHERE id = $1", device["id"])
        except (httpx.HTTPError, jwt.PyJWTError):
            continue


async def notify(
    database: asyncpg.Pool,
    user_id: UUID | str,
    kind: str,
    title: str,
    body: str,
    entity_type: str | None = None,
    entity_id: UUID | str | None = None,
    *,
    email: bool = False,
    idempotency_key: str | None = None,
    action_url: str | None = None,
) -> None:
    row = await database.fetchrow(
        """
        INSERT INTO notifications (user_id, kind, title, body, entity_type, entity_id, idempotency_key, action_url)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL
        DO NOTHING
        RETURNING id
        """,
        UUID(str(user_id)),
        kind,
        title,
        body,
        entity_type,
        UUID(str(entity_id)) if entity_id else None,
        idempotency_key,
        action_url,
    )
    if idempotency_key and not row:
        return
    await deliver_push(database, user_id, kind, title, body, entity_type, entity_id, action_url)


async def active_group_id(database: asyncpg.Pool, user_id: UUID | str) -> UUID | None:
    return await database.fetchval(
        "SELECT group_id FROM group_members WHERE user_id = $1 AND status = 'active' LIMIT 1",
        UUID(str(user_id)),
    )


async def require_no_active_group(database: asyncpg.Pool, user_id: UUID | str, allowed_group_id: UUID | str | None = None) -> None:
    current_group_id = await active_group_id(database, user_id)
    if current_group_id and (allowed_group_id is None or current_group_id != UUID(str(allowed_group_id))):
        raise HTTPException(status_code=409, detail="Leave your current group before joining another group")


async def require_group_creator(database: asyncpg.Pool, user_id: UUID | str, group_id: UUID | str) -> asyncpg.Record:
    member = await require_group_member(database, user_id, group_id)
    if member["role"] != "creator":
        raise HTTPException(status_code=403, detail="Group creator permission required")
    return member


async def require_group_member(database: asyncpg.Pool, user_id: UUID | str, group_id: UUID | str) -> asyncpg.Record:
    member = await database.fetchrow(
        """
        SELECT gm.*, cg.school_id, cg.name AS group_name
        FROM group_members gm
        JOIN carpool_groups cg ON cg.id = gm.group_id
        WHERE gm.group_id = $1 AND gm.user_id = $2 AND gm.status = 'active'
        """,
        UUID(str(group_id)),
        UUID(str(user_id)),
    )
    if not member:
        raise HTTPException(status_code=403, detail="Group membership required")
    return member


async def trip_with_permission(database: asyncpg.Pool, user_id: UUID | str, trip_id: UUID | str) -> asyncpg.Record:
    trip = await database.fetchrow("SELECT * FROM trips WHERE id = $1", UUID(str(trip_id)))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    await require_group_member(database, user_id, trip["group_id"])
    if trip["driver_user_id"] == UUID(str(user_id)):
        return trip
    assigned_parent = await database.fetchval(
        """
        SELECT 1
        FROM trip_children tc
        JOIN children c ON c.id = tc.child_id
        WHERE tc.trip_id = $1 AND c.parent_id = $2
        """,
        UUID(str(trip_id)),
        UUID(str(user_id)),
    )
    if assigned_parent:
        return trip
    creator = await database.fetchval("SELECT created_by FROM carpool_groups WHERE id = $1", trip["group_id"])
    if creator == UUID(str(user_id)):
        return trip
    proposed_swap = await database.fetchval(
        """
        SELECT 1
        FROM roster_change_requests
        WHERE trip_id = $1
          AND proposed_driver_user_id = $2
          AND request_type = 'swap'
        LIMIT 1
        """,
        UUID(str(trip_id)),
        UUID(str(user_id)),
    )
    if proposed_swap:
        return trip
    raise HTTPException(status_code=403, detail="Trip-specific permission required")


async def require_trip_driver(database: asyncpg.Pool, user_id: UUID | str, trip_id: UUID | str) -> asyncpg.Record:
    trip = await trip_with_permission(database, user_id, trip_id)
    if trip["driver_user_id"] != UUID(str(user_id)):
        raise HTTPException(status_code=403, detail="Assigned driver required")
    return trip


def trip_label(trip: asyncpg.Record | dict[str, Any]) -> str:
    return f"{trip['service_date']} {trip['expected_time']} {trip['trip_type']}"


async def trip_child_parent_rows(database: asyncpg.Pool, trip_id: UUID | str) -> list[asyncpg.Record]:
    return await database.fetch(
        """
        SELECT DISTINCT c.parent_id, c.name AS child_name
        FROM trip_children tc
        JOIN children c ON c.id = tc.child_id
        WHERE tc.trip_id = $1
        ORDER BY child_name
        """,
        UUID(str(trip_id)),
    )


async def affected_trip_user_ids(database: asyncpg.Pool, trip: asyncpg.Record | dict[str, Any], *, include_driver: bool = True, include_creator: bool = True) -> set[UUID]:
    user_ids = {row["parent_id"] for row in await trip_child_parent_rows(database, trip["id"])}
    if include_driver:
        user_ids.add(trip["driver_user_id"])
    if include_creator:
        user_ids.add(trip["created_by"])
    return {UUID(str(user_id)) for user_id in user_ids if user_id}


async def enforce_trip_child_access(database: asyncpg.Pool, user_id: UUID | str, trip_id: UUID | str, child_id: UUID | str) -> None:
    trip = await trip_with_permission(database, user_id, trip_id)
    if trip["driver_user_id"] == UUID(str(user_id)):
        return
    owns_child = await database.fetchval(
        """
        SELECT 1
        FROM trip_children tc
        JOIN children c ON c.id = tc.child_id
        WHERE tc.trip_id = $1 AND tc.child_id = $2 AND c.parent_id = $3
        """,
        UUID(str(trip_id)),
        UUID(str(child_id)),
        UUID(str(user_id)),
    )
    if not owns_child:
        raise HTTPException(status_code=403, detail="Child trip permission required")


async def visible_trip_children(database: asyncpg.Pool, user_id: UUID | str, trip: asyncpg.Record) -> list[dict[str, Any]]:
    is_driver = trip["driver_user_id"] == UUID(str(user_id))
    creator = await database.fetchval("SELECT created_by FROM carpool_groups WHERE id = $1", trip["group_id"])
    proposed_swap = await database.fetchval(
        """
        SELECT 1
        FROM roster_change_requests
        WHERE trip_id = $1
          AND proposed_driver_user_id = $2
          AND request_type = 'swap'
        LIMIT 1
        """,
        trip["id"],
        UUID(str(user_id)),
    )
    can_view_all = is_driver or creator == UUID(str(user_id)) or bool(proposed_swap)
    if can_view_all:
        rows = await database.fetch(
            """
            SELECT tc.*, c.name, c.year_group, c.pickup_notes, c.emergency_contact_name, c.emergency_contact_phone,
                   c.parent_id, c.home_address, c.home_city, c.home_country, c.home_latitude, c.home_longitude, c.home_place_id
            FROM trip_children tc
            JOIN children c ON c.id = tc.child_id
            WHERE tc.trip_id = $1
            ORDER BY c.name
            """,
            trip["id"],
        )
    else:
        rows = await database.fetch(
            """
            SELECT tc.*, c.name, c.year_group, c.pickup_notes, c.emergency_contact_name, c.emergency_contact_phone,
                   c.parent_id, c.home_address, c.home_city, c.home_country, c.home_latitude, c.home_longitude, c.home_place_id
            FROM trip_children tc
            JOIN children c ON c.id = tc.child_id
            WHERE tc.trip_id = $1 AND c.parent_id = $2
            ORDER BY c.name
            """,
            trip["id"],
            UUID(str(user_id)),
        )
    children = rows_to_list(rows)
    if not is_driver:
        for child in children:
            if child.get("parent_id") != str(user_id):
                child["home_address"] = None
                child["home_city"] = None
                child["home_country"] = None
                child["home_latitude"] = None
                child["home_longitude"] = None
                child["home_place_id"] = None
    return children


def child_home_address(child: asyncpg.Record | dict[str, Any]) -> str:
    getter = child.get if isinstance(child, dict) else child.__getitem__
    parts = []
    for key in ("home_address", "home_city", "home_country"):
        value = getter(key)
        if value:
            parts.append(str(value))
    return ", ".join(parts)


def route_waypoint_from_child(child: asyncpg.Record) -> dict[str, Any] | None:
    if child["home_latitude"] is not None and child["home_longitude"] is not None:
        return {
            "location": {
                "latLng": {
                    "latitude": float(child["home_latitude"]),
                    "longitude": float(child["home_longitude"]),
                }
            }
        }
    address = child_home_address(child)
    if address:
        return {"address": address}
    return None


def parse_google_duration_seconds(duration: str | None) -> int | None:
    if not duration or not duration.endswith("s"):
        return None
    try:
        seconds = float(duration[:-1])
    except ValueError:
        return None
    return max(0, int(round(seconds)))


def parse_google_distance_meters(distance: Any) -> int | None:
    try:
        meters = int(distance)
    except (TypeError, ValueError):
        return None
    return meters if meters >= 0 else None


def route_unavailable(reason: str, message: str, **diagnostics: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"route": None, "reason": reason, "message": message}
    if diagnostics:
        payload["diagnostics"] = diagnostics
    return payload


def safe_google_error(response: httpx.Response | None) -> dict[str, Any]:
    if response is None:
        return {}
    try:
        data = response.json()
    except ValueError:
        return {}
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return {}
    safe: dict[str, Any] = {}
    if error.get("status"):
        safe["status"] = error.get("status")
    if error.get("code"):
        safe["code"] = error.get("code")
    return safe


async def trip_route_children_for_user(database: asyncpg.Pool, user_id: UUID | str, trip: asyncpg.Record) -> tuple[list[asyncpg.Record], bool]:
    is_driver = trip["driver_user_id"] == UUID(str(user_id))
    creator = await database.fetchval("SELECT created_by FROM carpool_groups WHERE id = $1", trip["group_id"])
    proposed_swap = await database.fetchval(
        """
        SELECT 1
        FROM roster_change_requests
        WHERE trip_id = $1
          AND proposed_driver_user_id = $2
          AND request_type = 'swap'
        LIMIT 1
        """,
        trip["id"],
        UUID(str(user_id)),
    )
    can_view_all_stops = is_driver or creator == UUID(str(user_id)) or bool(proposed_swap)
    if can_view_all_stops:
        rows = await database.fetch(
            """
            SELECT c.id, c.name, c.home_address, c.home_city, c.home_country, c.home_latitude, c.home_longitude,
                   tc.pickup_status, tc.dropoff_status
            FROM trip_children tc
            JOIN children c ON c.id = tc.child_id
            WHERE tc.trip_id = $1
            ORDER BY c.name
            """,
            trip["id"],
        )
    else:
        rows = await database.fetch(
            """
            SELECT c.id, c.name, c.home_address, c.home_city, c.home_country, c.home_latitude, c.home_longitude,
                   tc.pickup_status, tc.dropoff_status
            FROM trip_children tc
            JOIN children c ON c.id = tc.child_id
            WHERE tc.trip_id = $1 AND c.parent_id = $2
            ORDER BY c.name
            """,
            trip["id"],
            UUID(str(user_id)),
        )
    return list(rows), can_view_all_stops


async def serialize_trip(database: asyncpg.Pool, user_id: UUID | str, trip: asyncpg.Record) -> dict[str, Any]:
    payload = row_to_dict(trip) or {}
    driver = await database.fetchrow("SELECT id, name, email, phone FROM users WHERE id = $1", trip["driver_user_id"])
    payload["driver"] = row_to_dict(driver)
    payload["children"] = await visible_trip_children(database, user_id, trip)
    response = await database.fetchrow(
        "SELECT status, note, created_at FROM trip_responses WHERE trip_id = $1 AND user_id = $2",
        trip["id"],
        UUID(str(user_id)),
    )
    payload["my_response"] = row_to_dict(response)
    latest = await database.fetchrow(
        "SELECT * FROM trip_locations WHERE trip_id = $1 ORDER BY received_at DESC LIMIT 1",
        trip["id"],
    )
    if latest:
        latest_payload = row_to_dict(latest) or {}
        received_at = latest["received_at"]
        age = (datetime.now(UTC) - received_at).total_seconds()
        latest_payload["age_seconds"] = int(age)
        latest_payload["fresh"] = age <= settings.location_stale_seconds
        payload["latest_location"] = latest_payload
    else:
        payload["latest_location"] = None
    return payload


async def serialize_trips(database: asyncpg.Pool, user_id: UUID | str, trips: list[asyncpg.Record]) -> list[dict[str, Any]]:
    if not trips:
        return []

    user_uuid = UUID(str(user_id))
    trip_ids = [trip["id"] for trip in trips]
    group_ids = list({trip["group_id"] for trip in trips})
    driver_ids = list({trip["driver_user_id"] for trip in trips})
    now = datetime.now(UTC)

    driver_rows, group_creator_rows, swap_rows, child_rows, response_rows, location_rows = await asyncio.gather(
        database.fetch("SELECT id, name, email, phone FROM users WHERE id = ANY($1::uuid[])", driver_ids),
        database.fetch("SELECT id, created_by FROM carpool_groups WHERE id = ANY($1::uuid[])", group_ids),
        database.fetch(
            """
            SELECT trip_id
            FROM roster_change_requests
            WHERE trip_id = ANY($1::uuid[])
              AND proposed_driver_user_id = $2
              AND request_type = 'swap'
            """,
            trip_ids,
            user_uuid,
        ),
        database.fetch(
            """
            SELECT tc.*, c.name, c.year_group, c.pickup_notes, c.emergency_contact_name, c.emergency_contact_phone,
                   c.parent_id, c.home_address, c.home_city, c.home_country, c.home_latitude, c.home_longitude, c.home_place_id
            FROM trip_children tc
            JOIN children c ON c.id = tc.child_id
            WHERE tc.trip_id = ANY($1::uuid[])
            ORDER BY c.name
            """,
            trip_ids,
        ),
        database.fetch(
            "SELECT trip_id, status, note, created_at FROM trip_responses WHERE trip_id = ANY($1::uuid[]) AND user_id = $2",
            trip_ids,
            user_uuid,
        ),
        database.fetch(
            """
            SELECT DISTINCT ON (trip_id) *
            FROM trip_locations
            WHERE trip_id = ANY($1::uuid[])
            ORDER BY trip_id, received_at DESC
            """,
            trip_ids,
        ),
    )

    drivers = {row["id"]: row_to_dict(row) for row in driver_rows}
    group_creators = {row["id"]: row["created_by"] for row in group_creator_rows}
    proposed_swap_trip_ids = {row["trip_id"] for row in swap_rows}
    responses = {row["trip_id"]: row_to_dict(row) for row in response_rows}
    locations: dict[UUID, dict[str, Any] | None] = {}
    for row in location_rows:
        location = row_to_dict(row) or {}
        age = (now - row["received_at"]).total_seconds()
        location["age_seconds"] = int(age)
        location["fresh"] = age <= settings.location_stale_seconds
        locations[row["trip_id"]] = location

    children_by_trip: dict[UUID, list[dict[str, Any]]] = {}
    for row in child_rows:
        child = row_to_dict(row) or {}
        children_by_trip.setdefault(row["trip_id"], []).append(child)

    payloads: list[dict[str, Any]] = []
    for trip in trips:
        is_driver = trip["driver_user_id"] == user_uuid
        can_view_all = is_driver or group_creators.get(trip["group_id"]) == user_uuid or trip["id"] in proposed_swap_trip_ids
        children: list[dict[str, Any]] = []
        for child in children_by_trip.get(trip["id"], []):
            if not can_view_all and child.get("parent_id") != str(user_uuid):
                continue
            child_payload = dict(child)
            if not is_driver and child_payload.get("parent_id") != str(user_uuid):
                child_payload["home_address"] = None
                child_payload["home_city"] = None
                child_payload["home_country"] = None
                child_payload["home_latitude"] = None
                child_payload["home_longitude"] = None
                child_payload["home_place_id"] = None
            children.append(child_payload)

        payload = row_to_dict(trip) or {}
        payload["driver"] = drivers.get(trip["driver_user_id"])
        payload["children"] = children
        payload["my_response"] = responses.get(trip["id"])
        payload["latest_location"] = locations.get(trip["id"])
        payloads.append(payload)
    return payloads


@app.get("/", include_in_schema=False)
async def web_app() -> FileResponse:
    return FileResponse("app/static/index.html")


@app.get("/api/health")
async def health(database: DbDep) -> dict[str, str]:
    await database.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/api/config")
async def web_config() -> dict[str, Any]:
    return {
        "app_name": settings.app_name,
        "public_site_url": settings.public_site_url,
        "google_maps_browser_key": settings.google_maps_browser_key,
        "routes_enabled": bool(settings.google_routes_api_key),
        "location_stale_seconds": settings.location_stale_seconds,
        "safety_timeout_minutes": settings.safety_timeout_minutes,
    }


@app.get("/api/pending-actions")
async def list_pending_actions(user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    user_id = UUID(user["id"])
    invitation_rows = await database.fetch(
        """
        SELECT id, token, email, expires_at, created_at
        FROM invitations
        WHERE email = lower($1)
          AND status = 'pending'
          AND expires_at > now()
        ORDER BY created_at DESC
        """,
        user["email"],
    )
    assignment_rows = await database.fetch(
        """
        SELECT t.*, driver.name AS driver_name
        FROM trips t
        JOIN users driver ON driver.id = t.driver_user_id
        JOIN group_members gm
          ON gm.group_id = t.group_id
         AND gm.user_id = $1
         AND gm.status = 'active'
        LEFT JOIN trip_responses tr
          ON tr.trip_id = t.id
         AND tr.user_id = $1
        WHERE t.driver_user_id = $1
          AND t.status IN ('planned', 'delayed')
          AND t.service_date >= CURRENT_DATE
          AND tr.id IS NULL
        ORDER BY t.service_date, t.expected_time
        """,
        user_id,
    )
    swap_rows = await database.fetch(
        """
        SELECT rcr.*, t.service_date, t.trip_type, t.expected_time, requester.name AS requester_name
        FROM roster_change_requests rcr
        JOIN trips t ON t.id = rcr.trip_id
        JOIN users requester ON requester.id = rcr.requested_by
        JOIN group_members gm
          ON gm.group_id = t.group_id
         AND gm.user_id = $1
         AND gm.status = 'active'
        WHERE rcr.request_type = 'swap'
          AND rcr.status = 'open'
          AND rcr.proposed_driver_user_id = $1
          AND t.status IN ('planned', 'delayed')
          AND t.service_date >= CURRENT_DATE
        ORDER BY t.service_date, t.expected_time
        """,
        user_id,
    )

    actions: list[dict[str, Any]] = []
    for invitation in invitation_rows:
        actions.append(
            {
                "type": "invitation",
                "id": str(invitation["id"]),
                "title": "SchoolHop invitation",
                "body": "Accept or decline this carpool group invitation.",
                "action_url": f"/?invite={invitation['token']}",
                "created_at": serialize_value(invitation["created_at"]),
            }
        )
    for trip in assignment_rows:
        actions.append(
            {
                "type": "driver_assignment",
                "id": str(trip["id"]),
                "trip_id": str(trip["id"]),
                "title": "Driver assignment",
                "body": f"Accept or decline {trip_label(trip)}.",
                "action_url": f"/?tab=trip&trip={trip['id']}",
                "created_at": serialize_value(trip["created_at"]),
            }
        )
    for request in swap_rows:
        actions.append(
            {
                "type": "swap_request",
                "id": str(request["id"]),
                "trip_id": str(request["trip_id"]),
                "title": "Trip swap request received",
                "body": f"{request['requester_name']} asked you to cover {trip_label(request)}.",
                "action_url": f"/?tab=trip&trip={request['trip_id']}&swap_request={request['id']}",
                "created_at": serialize_value(request["created_at"]),
            }
        )
    return actions


@app.post("/api/client-errors")
async def client_errors(payload: ClientErrorIn) -> dict[str, str]:
    print(
        "CLIENT_ERROR "
        + json.dumps(
            {
                "message": payload.message,
                "source": payload.source,
                "lineno": payload.lineno,
                "colno": payload.colno,
                "stack": payload.stack,
                "user_agent": payload.user_agent,
            },
            ensure_ascii=True,
        ),
        flush=True,
    )
    return {"status": "ok"}


@app.post("/api/auth/register")
async def register(payload: RegisterIn, database: DbDep) -> dict[str, Any]:
    if not can_expose_development_codes():
        raise HTTPException(status_code=410, detail="Use verified registration")
    existing = await database.fetchval("SELECT 1 FROM users WHERE email = lower($1)", payload.email)
    if existing:
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = await database.fetchrow(
        """
        INSERT INTO users (email, password_hash, name, phone, email_verified_at)
        VALUES (lower($1), $2, $3, $4, now())
        RETURNING id, email, name, phone, email_verified_at, created_at
        """,
        payload.email,
        passwords.hash(payload.password),
        payload.name,
        payload.phone,
    )
    await audit(database, user["id"], "user.registered")
    return {"token": create_token(user["id"]), "user": row_to_dict(user)}


@app.post("/api/auth/register/start")
async def register_start(payload: RegisterStartIn, database: DbDep) -> dict[str, Any]:
    email = normalize_email(payload.email)
    existing = await database.fetchval("SELECT 1 FROM users WHERE email = $1", email)
    if existing:
        raise HTTPException(status_code=409, detail="Email is already registered")
    code = verification_code()
    token = f"registration_{secrets.token_urlsafe(32)}"
    async with database.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE email_verifications
                SET used_at = now()
                WHERE email = $1 AND purpose = 'registration' AND used_at IS NULL
                """,
                email,
            )
            await conn.execute(
                """
                INSERT INTO email_verifications (email, token, code_hash, purpose, name, phone, expires_at, last_sent_at)
                VALUES ($1, $2, $3, 'registration', $4, $5, now() + interval '15 minutes', now())
                """,
                email,
                token,
                verification_hash(email, code, "registration"),
                payload.name,
                payload.phone,
            )
    delivery_status = await send_email(
        email,
        "Your SchoolHop verification code",
        f"Your SchoolHop verification code is {code}. It expires in 15 minutes.",
    )
    return delivery_payload(email, delivery_status, code)


@app.post("/api/auth/register/resend")
async def register_resend(payload: RegisterStartIn, database: DbDep) -> dict[str, Any]:
    email = normalize_email(payload.email)
    verification = await database.fetchrow(
        """
        SELECT * FROM email_verifications
        WHERE email = $1 AND purpose = 'registration' AND used_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        email,
    )
    if not verification:
        return await register_start(payload, database)
    if verification["last_sent_at"] and verification["last_sent_at"] + timedelta(seconds=60) > datetime.now(UTC):
        wait = int((verification["last_sent_at"] + timedelta(seconds=60) - datetime.now(UTC)).total_seconds()) + 1
        raise HTTPException(status_code=429, detail=f"Please wait {wait} seconds before requesting another code")
    code = verification_code()
    await database.execute(
        """
        UPDATE email_verifications
        SET code_hash = $2, name = $3, phone = $4, expires_at = now() + interval '15 minutes',
            last_sent_at = now(), attempt_count = 0
        WHERE id = $1
        """,
        verification["id"],
        verification_hash(email, code, "registration"),
        payload.name,
        payload.phone,
    )
    delivery_status = await send_email(
        email,
        "Your SchoolHop verification code",
        f"Your SchoolHop verification code is {code}. It expires in 15 minutes.",
    )
    return delivery_payload(email, delivery_status, code)


@app.post("/api/auth/register/verify")
async def register_verify(payload: RegisterVerifyIn, database: DbDep) -> dict[str, Any]:
    email = normalize_email(payload.email)
    verification = await database.fetchrow(
        """
        SELECT * FROM email_verifications
        WHERE email = $1 AND purpose = 'registration' AND used_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        email,
    )
    if not verification:
        raise HTTPException(status_code=400, detail="Start account creation again to request a new code")
    if verification["expires_at"] < datetime.now(UTC):
        raise HTTPException(status_code=400, detail="Verification code has expired")
    if verification["attempt_count"] >= 5:
        raise HTTPException(status_code=429, detail="Too many incorrect attempts. Request a new code")
    if verification["code_hash"] != verification_hash(email, payload.code, "registration"):
        await database.execute("UPDATE email_verifications SET attempt_count = attempt_count + 1 WHERE id = $1", verification["id"])
        raise HTTPException(status_code=400, detail="That verification code is not correct")
    await database.execute("UPDATE email_verifications SET used_at = now() WHERE id = $1", verification["id"])
    return {"status": "verified", "email": email, "masked_email": mask_email(email), "registration_token": verification["token"]}


@app.post("/api/auth/register/complete")
async def register_complete(payload: RegisterCompleteIn, database: DbDep) -> dict[str, Any]:
    email = normalize_email(payload.email)
    existing = await database.fetchval("SELECT 1 FROM users WHERE email = $1", email)
    if existing:
        raise HTTPException(status_code=409, detail="Email is already registered")
    verification = await database.fetchrow(
        """
        SELECT * FROM email_verifications
        WHERE email = $1 AND token = $2 AND purpose = 'registration' AND used_at IS NOT NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        email,
        payload.registration_token,
    )
    if not verification:
        raise HTTPException(status_code=400, detail="Verify your email before creating a password")
    user = await database.fetchrow(
        """
        INSERT INTO users (email, password_hash, name, phone, email_verified_at)
        VALUES ($1, $2, $3, $4, now())
        RETURNING id, email, name, phone, email_verified_at, created_at
        """,
        email,
        passwords.hash(payload.password),
        verification["name"],
        verification["phone"],
    )
    await database.execute("UPDATE email_verifications SET purpose = 'registration_completed' WHERE id = $1", verification["id"])
    await audit(database, user["id"], "user.registered")
    return {"token": create_token(user["id"]), "user": row_to_dict(user)}


@app.post("/api/auth/login")
async def login(payload: LoginIn, database: DbDep) -> dict[str, Any]:
    user = await database.fetchrow("SELECT * FROM users WHERE email = lower($1) AND status = 'active'", payload.email)
    if not user or not passwords.verify(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user["email_verified_at"]:
        raise HTTPException(status_code=403, detail="Email verification required")
    return {"token": create_token(user["id"]), "user": row_to_dict(user)}


@app.post("/api/auth/password-reset/start")
async def password_reset_start(payload: PasswordResetStartIn, database: DbDep) -> dict[str, Any]:
    generic_message = "If an account exists for this email, a password reset link has been sent."
    email = normalize_email(payload.email)
    user = await database.fetchrow("SELECT id, email FROM users WHERE email = $1 AND status = 'active'", email)
    reset_token: str | None = None
    delivery_status: str | None = None
    if user:
        reset_token = secrets.token_urlsafe(32)
        async with database.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE password_reset_tokens SET used_at = now() WHERE user_id = $1 AND used_at IS NULL",
                    user["id"],
                )
                await conn.execute(
                    """
                    INSERT INTO password_reset_tokens (user_id, token_hash, expires_at)
                    VALUES ($1, $2, now() + interval '30 minutes')
                    """,
                    user["id"],
                    reset_token_hash(reset_token),
                )
        reset_url = f"{settings.public_site_url.rstrip('/')}/?reset={reset_token}"
        delivery_status = await send_email(
            user["email"],
            "Reset your SchoolHop password",
            f"Open this secure link to reset your SchoolHop password: {reset_url}\n\nThis link expires in 30 minutes and can be used once.",
        )
    response: dict[str, Any] = {"status": "ok", "message": generic_message}
    if can_expose_development_codes() and reset_token:
        response["reset_token"] = reset_token
        response["reset_url"] = f"{settings.public_site_url.rstrip('/')}/?reset={reset_token}"
        response["delivery_status"] = delivery_status
    return response


@app.post("/api/auth/password-reset/complete")
async def password_reset_complete(payload: PasswordResetCompleteIn, database: DbDep) -> dict[str, str]:
    token_hash = reset_token_hash(payload.token)
    async with database.acquire() as conn:
        async with conn.transaction():
            reset = await conn.fetchrow(
                """
                SELECT prt.*, u.id AS account_user_id
                FROM password_reset_tokens prt
                JOIN users u ON u.id = prt.user_id AND u.status = 'active'
                WHERE prt.token_hash = $1 AND prt.used_at IS NULL AND prt.expires_at > now()
                FOR UPDATE OF prt
                """,
                token_hash,
            )
            if not reset:
                raise HTTPException(status_code=400, detail="Password reset link is invalid or expired")
            await conn.execute("UPDATE users SET password_hash = $2 WHERE id = $1", reset["account_user_id"], passwords.hash(payload.password))
            await conn.execute("UPDATE password_reset_tokens SET used_at = now() WHERE id = $1", reset["id"])
            await conn.execute(
                """
                INSERT INTO audit_records (actor_user_id, action, details)
                VALUES ($1, 'user.password_reset', '{}'::jsonb)
                """,
                reset["account_user_id"],
            )
    return {"status": "ok", "message": "Your password has been reset. You can sign in now."}


@app.get("/api/me")
async def me(user: UserDep) -> dict[str, Any]:
    return {"user": {k: v for k, v in user.items() if k != "password_hash"}}


@app.post("/api/mobile/devices")
async def register_mobile_device(payload: MobileDeviceIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    row = await database.fetchrow(
        """
        INSERT INTO mobile_devices (user_id, provider, token, platform, enabled, last_seen_at)
        VALUES ($1, $2, $3, $4, true, now())
        ON CONFLICT (provider, token)
        DO UPDATE SET user_id = EXCLUDED.user_id, platform = EXCLUDED.platform, enabled = true, last_seen_at = now()
        RETURNING id, provider, platform, enabled, last_seen_at, created_at
        """,
        UUID(user["id"]),
        payload.provider,
        payload.token,
        payload.platform,
    )
    await audit(database, user["id"], "mobile_device.registered", details={"provider": payload.provider, "platform": payload.platform})
    return row_to_dict(row) or {}


@app.get("/api/schools")
async def list_schools(database: DbDep, q: str | None = None) -> list[dict[str, Any]]:
    if q:
        rows = await database.fetch(
            "SELECT * FROM schools WHERE name ILIKE $1 OR city ILIKE $1 ORDER BY name LIMIT 50",
            f"%{q}%",
        )
    else:
        rows = await database.fetch("SELECT * FROM schools ORDER BY name LIMIT 50")
    return rows_to_list(rows)


@app.post("/api/schools")
async def create_school(payload: SchoolIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    row = await database.fetchrow(
        """
        INSERT INTO schools (name, address, city, country, created_by)
        VALUES ($1, $2, $3, upper($4), $5)
        ON CONFLICT (name, city) DO UPDATE SET address = COALESCE(EXCLUDED.address, schools.address)
        RETURNING *
        """,
        payload.name,
        payload.address,
        payload.city,
        payload.country,
        UUID(user["id"]),
    )
    await audit(database, user["id"], "school.upserted", details={"school_id": str(row["id"])})
    return row_to_dict(row) or {}


@app.get("/api/groups")
async def list_groups(user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    rows = await database.fetch(
        """
        SELECT cg.*, s.name AS school_name, gm.role
        FROM group_members gm
        JOIN carpool_groups cg ON cg.id = gm.group_id
        JOIN schools s ON s.id = cg.school_id
        WHERE gm.user_id = $1 AND gm.status = 'active'
        ORDER BY cg.created_at DESC
        """,
        UUID(user["id"]),
    )
    return rows_to_list(rows)


@app.get("/api/groups/discover")
async def discover_groups(user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    rows = await database.fetch(
        """
        SELECT cg.id, cg.name, cg.created_at,
               s.id AS school_id, s.name AS school_name, s.city AS school_city
        FROM carpool_groups cg
        JOIN schools s ON s.id = cg.school_id
        ORDER BY s.name, s.city, cg.name
        LIMIT 200
        """
    )
    return rows_to_list(rows)


@app.post("/api/groups")
async def create_group(payload: GroupIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_no_active_group(database, user["id"])
    async with database.acquire() as conn:
        async with conn.transaction():
            group = await conn.fetchrow(
                """
                INSERT INTO carpool_groups (school_id, name, created_by)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                payload.school_id,
                payload.name,
                UUID(user["id"]),
            )
            await conn.execute(
                "INSERT INTO group_members (group_id, user_id, role) VALUES ($1, $2, 'creator')",
                group["id"],
                UUID(user["id"]),
            )
    await audit(database, user["id"], "group.created", group["id"])
    return row_to_dict(group) or {}


@app.get("/api/groups/{group_id}")
async def group_detail(group_id: UUID, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_group_member(database, user["id"], group_id)
    group = await database.fetchrow(
        """
        SELECT cg.*, s.name AS school_name, s.city AS school_city
        FROM carpool_groups cg JOIN schools s ON s.id = cg.school_id
        WHERE cg.id = $1
        """,
        group_id,
    )
    members = await database.fetch(
        """
        SELECT u.id, u.name, u.email, u.phone, gm.role, gm.joined_at
        FROM group_members gm JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = $1 AND gm.status = 'active'
        ORDER BY u.name
        """,
        group_id,
    )
    invitations = await database.fetch(
        "SELECT id, email, token, status, expires_at, created_at FROM invitations WHERE group_id = $1 ORDER BY created_at DESC",
        group_id,
    )
    join_requests: list[asyncpg.Record] = []
    if group["created_by"] == UUID(user["id"]):
        join_requests = await database.fetch(
            """
            SELECT gjr.*, u.name AS requester_name, u.email AS requester_email
            FROM group_join_requests gjr
            JOIN users u ON u.id = gjr.requester_user_id
            WHERE gjr.group_id = $1 AND gjr.status = 'pending'
            ORDER BY gjr.created_at DESC
            """,
            group_id,
        )
    return {"group": row_to_dict(group), "members": rows_to_list(members), "invitations": rows_to_list(invitations), "join_requests": rows_to_list(join_requests)}


@app.post("/api/groups/access-requests")
async def request_group_access(payload: JoinRequestIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_no_active_group(database, user["id"])
    group = await database.fetchrow(
        """
        SELECT cg.*, s.name AS school_name, s.city AS school_city
        FROM carpool_groups cg
        JOIN schools s ON s.id = cg.school_id
        WHERE cg.id = $1
        """,
        payload.group_id,
    )
    if not group:
        raise HTTPException(status_code=404, detail="SchoolHop group not found")
    row = await database.fetchrow(
        """
        INSERT INTO group_join_requests (group_id, requester_user_id, requester_note, status, updated_at)
        VALUES ($1, $2, $3, 'pending', now())
        ON CONFLICT (group_id, requester_user_id)
        DO UPDATE SET requester_note = EXCLUDED.requester_note,
                      status = 'pending',
                      decided_by = NULL,
                      decided_at = NULL,
                      updated_at = now()
        RETURNING *
        """,
        payload.group_id,
        UUID(user["id"]),
        payload.note,
    )
    await notify(
        database,
        group["created_by"],
        "group_access_request",
        "SchoolHop access request",
        f"{user['name']} ({user['email']}) requested access to {group['name']} at {group['school_name']}.",
        "group",
        group["id"],
        email=True,
        idempotency_key=f"group_access_request:{row['id']}:{group['created_by']}",
        action_url="/?tab=group",
    )
    await audit(database, user["id"], "group.access_requested", group["id"], details={"request_id": str(row["id"])})
    return row_to_dict(row) or {}


@app.post("/api/groups/{group_id}/access-requests/{request_id}/decision")
async def decide_group_access_request(group_id: UUID, request_id: UUID, payload: JoinRequestDecisionIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_group_creator(database, user["id"], group_id)
    request = await database.fetchrow(
        """
        SELECT gjr.*, u.name AS requester_name, u.email AS requester_email
        FROM group_join_requests gjr
        JOIN users u ON u.id = gjr.requester_user_id
        WHERE gjr.id = $1 AND gjr.group_id = $2
        """,
        request_id,
        group_id,
    )
    if not request:
        raise HTTPException(status_code=404, detail="Access request not found")
    if request["status"] != "pending":
        raise HTTPException(status_code=409, detail="Access request already decided")
    if payload.status == "approved":
        await require_no_active_group(database, request["requester_user_id"], group_id)
    async with database.acquire() as conn:
        async with conn.transaction():
            if payload.status == "approved":
                await conn.execute(
                    """
                    INSERT INTO group_members (group_id, user_id, role, status)
                    VALUES ($1, $2, 'member', 'active')
                    ON CONFLICT (group_id, user_id) DO UPDATE SET status = 'active'
                    """,
                    group_id,
                    request["requester_user_id"],
                )
            row = await conn.fetchrow(
                """
                UPDATE group_join_requests
                SET status = $3, decided_by = $4, decided_at = now(), updated_at = now()
                WHERE id = $1 AND group_id = $2
                RETURNING *
                """,
                request_id,
                group_id,
                payload.status,
                UUID(user["id"]),
            )
    await notify(
        database,
        request["requester_user_id"],
        "group_access_decision",
        f"SchoolHop access {payload.status}",
        payload.note or f"Your request to join the SchoolHop group was {payload.status}.",
        "group",
        group_id,
        email=True,
        idempotency_key=f"group_access_decision:{request_id}:{payload.status}:{request['requester_user_id']}",
        action_url="/?tab=group",
    )
    await audit(database, user["id"], "group.access_decided", group_id, details={"request_id": str(request_id), "status": payload.status})
    return row_to_dict(row) or {}


@app.post("/api/groups/{group_id}/invitations")
async def invite_parent(group_id: UUID, payload: InviteIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_group_member(database, user["id"], group_id)
    token = secrets.token_urlsafe(32)
    invitation = await database.fetchrow(
        """
        INSERT INTO invitations (group_id, email, token, invited_by)
        VALUES ($1, lower($2), $3, $4)
        RETURNING id, email, token, status, expires_at, created_at
        """,
        group_id,
        payload.email,
        token,
        UUID(user["id"]),
    )
    invited_user = await database.fetchrow("SELECT id FROM users WHERE email = lower($1)", payload.email)
    invite_url = f"{settings.public_site_url.rstrip('/')}/?invite={token}"
    if invited_user:
        await notify(
            database,
            invited_user["id"],
            "invitation",
            f"{user['name']} invited you to join SchoolHop",
            f"{user['name']} invited you to join a SchoolHop carpool group.",
            "invitation",
            invitation["id"],
            email=False,
            idempotency_key=f"group_invitation:{invitation['id']}:{invited_user['id']}",
            action_url=f"/?invite={token}",
        )
    await audit(database, user["id"], "group.invitation_created", group_id, details={"email": payload.email})
    payload_out = row_to_dict(invitation) or {}
    payload_out["delivery_status"] = "not_sent_activity_email_disabled"
    payload_out["invite_url"] = invite_url
    return payload_out


@app.post("/api/invitations/{token}/accept")
async def accept_invitation(token: str, user: UserDep, database: DbDep) -> dict[str, Any]:
    invitation = await database.fetchrow(
        "SELECT * FROM invitations WHERE token = $1 AND status = 'pending' AND expires_at > now()",
        token,
    )
    if not invitation:
        raise HTTPException(status_code=404, detail="Invitation not found or expired")
    if invitation["email"].lower() != user["email"].lower():
        raise HTTPException(status_code=403, detail="Invitation email does not match this account")
    await require_no_active_group(database, user["id"], invitation["group_id"])
    async with database.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO group_members (group_id, user_id, role)
                VALUES ($1, $2, 'member')
                ON CONFLICT (group_id, user_id) DO UPDATE SET status = 'active'
                """,
                invitation["group_id"],
                UUID(user["id"]),
            )
            await conn.execute(
                "UPDATE invitations SET status = 'accepted', accepted_by = $1, accepted_at = now() WHERE id = $2",
                UUID(user["id"]),
                invitation["id"],
            )
    await audit(database, user["id"], "group.invitation_accepted", invitation["group_id"])
    return {"group_id": str(invitation["group_id"])}


@app.post("/api/groups/{group_id}/leave")
async def leave_group(group_id: UUID, user: UserDep, database: DbDep) -> dict[str, str]:
    member = await require_group_member(database, user["id"], group_id)
    active_members = await database.fetchval(
        "SELECT count(*) FROM group_members WHERE group_id = $1 AND status = 'active'",
        group_id,
    )
    if member["role"] == "creator" and active_members and active_members > 1:
        raise HTTPException(status_code=409, detail="Remove other members before the creator leaves the group")
    await database.execute(
        "UPDATE group_members SET status = 'removed' WHERE group_id = $1 AND user_id = $2",
        group_id,
        UUID(user["id"]),
    )
    await audit(database, user["id"], "group.left", group_id)
    return {"status": "ok"}


@app.post("/api/groups/{group_id}/members/{member_user_id}/remove")
async def remove_group_member(group_id: UUID, member_user_id: UUID, user: UserDep, database: DbDep) -> dict[str, str]:
    await require_group_creator(database, user["id"], group_id)
    if member_user_id == UUID(user["id"]):
        raise HTTPException(status_code=400, detail="Use leave group to remove yourself")
    row = await database.fetchrow(
        """
        UPDATE group_members
        SET status = 'removed'
        WHERE group_id = $1 AND user_id = $2 AND status = 'active'
        RETURNING *
        """,
        group_id,
        member_user_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Active group member not found")
    await notify(database, member_user_id, "group_removed", "Removed from SchoolHop group", "You were removed from a SchoolHop carpool group.", "group", group_id, email=True, action_url="/?tab=group")
    await audit(database, user["id"], "group.member_removed", group_id, details={"member_user_id": str(member_user_id)})
    return {"status": "ok"}


@app.get("/api/children")
async def list_children(user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    rows = await database.fetch(
        """
        SELECT c.*, s.name AS school_name
        FROM children c JOIN schools s ON s.id = c.school_id
        WHERE c.parent_id = $1 AND c.status = 'active'
        ORDER BY c.name
        """,
        UUID(user["id"]),
    )
    return rows_to_list(rows)


@app.post("/api/children")
async def create_child(payload: ChildIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    row = await database.fetchrow(
        """
        INSERT INTO children (
            parent_id, school_id, name, year_group, pickup_notes,
            home_address, home_city, home_country, home_latitude, home_longitude, home_place_id,
            emergency_contact_name, emergency_contact_phone
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
        RETURNING *
        """,
        UUID(user["id"]),
        payload.school_id,
        payload.name,
        payload.year_group,
        payload.pickup_notes,
        payload.home_address,
        payload.home_city,
        payload.home_country,
        payload.home_latitude,
        payload.home_longitude,
        payload.home_place_id,
        payload.emergency_contact_name,
        payload.emergency_contact_phone,
    )
    await audit(database, user["id"], "child.created", details={"child_id": str(row["id"])})
    return row_to_dict(row) or {}


@app.patch("/api/children/{child_id}")
async def update_child(child_id: UUID, payload: ChildIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    row = await database.fetchrow(
        """
        UPDATE children
        SET school_id = $1,
            name = $2,
            year_group = $3,
            pickup_notes = $4,
            home_address = $5,
            home_city = $6,
            home_country = $7,
            home_latitude = $8,
            home_longitude = $9,
            home_place_id = $10,
            emergency_contact_name = $11,
            emergency_contact_phone = $12
        WHERE id = $13 AND parent_id = $14 AND status = 'active'
        RETURNING *
        """,
        payload.school_id,
        payload.name,
        payload.year_group,
        payload.pickup_notes,
        payload.home_address,
        payload.home_city,
        payload.home_country,
        payload.home_latitude,
        payload.home_longitude,
        payload.home_place_id,
        payload.emergency_contact_name,
        payload.emergency_contact_phone,
        child_id,
        UUID(user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Child not found")
    await audit(database, user["id"], "child.updated", details={"child_id": str(child_id)})
    return row_to_dict(row) or {}


@app.delete("/api/children/{child_id}")
async def remove_child(child_id: UUID, user: UserDep, database: DbDep) -> dict[str, str]:
    row = await database.fetchrow(
        """
        UPDATE children
        SET status = 'removed'
        WHERE id = $1 AND parent_id = $2 AND status = 'active'
        RETURNING *
        """,
        child_id,
        UUID(user["id"]),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Child not found")
    await audit(database, user["id"], "child.removed", details={"child_id": str(child_id)})
    return {"status": "ok"}


@app.get("/api/groups/{group_id}/children")
async def group_children(group_id: UUID, user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    await require_group_member(database, user["id"], group_id)
    rows = await database.fetch(
        """
        SELECT DISTINCT c.id, c.parent_id, c.school_id, c.name, c.year_group, c.pickup_notes,
               c.emergency_contact_name, c.emergency_contact_phone, c.created_at, u.name AS parent_name
        FROM children c
        JOIN users u ON u.id = c.parent_id
        JOIN carpool_groups cg ON cg.school_id = c.school_id
        JOIN group_members gm ON gm.group_id = cg.id AND gm.user_id = c.parent_id AND gm.status = 'active'
        WHERE cg.id = $1 AND c.status = 'active'
        ORDER BY c.name
        """,
        group_id,
    )
    return rows_to_list(rows)


@app.post("/api/groups/{group_id}/children/{child_id}/driver-approvals")
async def approve_driver(group_id: UUID, child_id: UUID, payload: DriverApprovalIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_group_member(database, user["id"], group_id)
    child = await database.fetchrow("SELECT * FROM children WHERE id = $1 AND parent_id = $2 AND status = 'active'", child_id, UUID(user["id"]))
    if not child:
        raise HTTPException(status_code=403, detail="Only a child's parent can approve drivers")
    await require_group_member(database, payload.driver_user_id, group_id)
    row = await database.fetchrow(
        """
        INSERT INTO driver_approvals (group_id, child_id, driver_user_id, approved, approved_by, updated_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (group_id, child_id, driver_user_id)
        DO UPDATE SET approved = EXCLUDED.approved, approved_by = EXCLUDED.approved_by, updated_at = now()
        RETURNING *
        """,
        group_id,
        child_id,
        payload.driver_user_id,
        payload.approved,
        UUID(user["id"]),
    )
    await audit(database, user["id"], "driver_approval.updated", group_id, details={"child_id": str(child_id), "driver_user_id": str(payload.driver_user_id), "approved": payload.approved})
    return row_to_dict(row) or {}


@app.get("/api/groups/{group_id}/driver-approvals")
async def list_driver_approvals(group_id: UUID, user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    await require_group_member(database, user["id"], group_id)
    rows = await database.fetch(
        """
        SELECT da.*, c.name AS child_name, u.name AS driver_name
        FROM driver_approvals da
        JOIN children c ON c.id = da.child_id
        JOIN users u ON u.id = da.driver_user_id
        WHERE da.group_id = $1
          AND (c.parent_id = $2 OR da.driver_user_id = $2)
        ORDER BY c.name, u.name
        """,
        group_id,
        UUID(user["id"]),
    )
    return rows_to_list(rows)


@app.post("/api/groups/{group_id}/trips")
async def create_trip(group_id: UUID, payload: TripIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    await require_group_member(database, user["id"], group_id)
    await require_group_member(database, payload.driver_user_id, group_id)
    child_rows = await database.fetch(
        """
        SELECT c.*
        FROM children c
        JOIN carpool_groups cg ON cg.school_id = c.school_id
        WHERE cg.id = $1 AND c.id = ANY($2::uuid[]) AND c.status = 'active'
        """,
        group_id,
        payload.child_ids,
    )
    if len(child_rows) != len(set(payload.child_ids)):
        raise HTTPException(status_code=400, detail="All children must attend the group's school")
    missing = []
    for child in child_rows:
        approved = await database.fetchval(
            """
            SELECT approved FROM driver_approvals
            WHERE group_id = $1 AND child_id = $2 AND driver_user_id = $3
            """,
            group_id,
            child["id"],
            payload.driver_user_id,
        )
        if not approved:
            missing.append(child["name"])
    if missing:
        raise HTTPException(status_code=403, detail=f"Driver approval missing for: {', '.join(missing)}")

    async with database.acquire() as conn:
        async with conn.transaction():
            trip = await conn.fetchrow(
                """
                INSERT INTO trips (group_id, driver_user_id, service_date, trip_type, expected_time, created_by)
                VALUES ($1, $2, $3, $4, $5::time, $6)
                RETURNING *
                """,
                group_id,
                payload.driver_user_id,
                payload.service_date,
                payload.trip_type,
                payload.expected_time,
                UUID(user["id"]),
            )
            for child_id in payload.child_ids:
                await conn.execute("INSERT INTO trip_children (trip_id, child_id) VALUES ($1, $2)", trip["id"], child_id)
    participant_ids = {payload.driver_user_id}
    participant_ids.update(child["parent_id"] for child in child_rows)
    for participant_id in participant_ids:
        if participant_id == payload.driver_user_id:
            await notify(
                database,
                participant_id,
                "driver_assignment",
                "SchoolHop driver assignment",
                "You were assigned to drive a carpool trip. Please accept or decline the assignment.",
                "trip",
                trip["id"],
                email=True,
                idempotency_key=f"driver_assignment:{trip['id']}:{participant_id}",
                action_url=f"/?tab=trip&trip={trip['id']}",
            )
        else:
            child_names = ", ".join(child["name"] for child in child_rows if child["parent_id"] == participant_id)
            await notify(
                database,
                participant_id,
                "roster",
                "SchoolHop trip assigned",
                f"{child_names or 'Your child'} has a {trip_label(trip)} trip with an assigned driver.",
                "trip",
                trip["id"],
                email=True,
                idempotency_key=f"parent_assignment:{trip['id']}:{participant_id}",
                action_url=f"/?tab=roster&trip={trip['id']}",
            )
    await audit(database, user["id"], "trip.created", group_id, trip["id"], {"child_ids": [str(x) for x in payload.child_ids]})
    return await serialize_trip(database, user["id"], trip)


@app.get("/api/groups/{group_id}/trips")
async def list_trips(group_id: UUID, user: UserDep, database: DbDep, from_date: date | None = None, to_date: date | None = None) -> list[dict[str, Any]]:
    await require_group_member(database, user["id"], group_id)
    effective_from_date = from_date or date.today()
    rows = await database.fetch(
        """
        SELECT DISTINCT t.*
        FROM trips t
        LEFT JOIN trip_children tc ON tc.trip_id = t.id
        LEFT JOIN children c ON c.id = tc.child_id
        WHERE t.group_id = $1
          AND t.service_date >= $2
          AND ($3::date IS NULL OR t.service_date <= $3)
          AND t.status NOT IN ('completed', 'cancelled', 'driver_unavailable', 'ignored')
          AND (
              t.driver_user_id = $4
              OR c.parent_id = $4
              OR t.created_by = $4
              OR EXISTS (
                  SELECT 1
                  FROM roster_change_requests rcr
                  WHERE rcr.trip_id = t.id
                    AND rcr.proposed_driver_user_id = $4
                    AND rcr.request_type = 'swap'
              )
          )
        ORDER BY t.service_date, t.expected_time
        """,
        group_id,
        effective_from_date,
        to_date,
        UUID(user["id"]),
    )
    return await serialize_trips(database, user["id"], list(rows))


@app.get("/api/groups/{group_id}/trips/history")
async def list_trip_history(group_id: UUID, user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    await require_group_member(database, user["id"], group_id)
    rows = await database.fetch(
        """
        SELECT DISTINCT t.*
        FROM trips t
        LEFT JOIN trip_children tc ON tc.trip_id = t.id
        LEFT JOIN children c ON c.id = tc.child_id
        WHERE t.group_id = $1
          AND (t.service_date < CURRENT_DATE OR t.status IN ('completed', 'cancelled', 'driver_unavailable', 'ignored'))
          AND (t.driver_user_id = $2 OR c.parent_id = $2 OR t.created_by = $2)
        ORDER BY t.service_date DESC, t.expected_time DESC
        LIMIT 200
        """,
        group_id,
        UUID(user["id"]),
    )
    return await serialize_trips(database, user["id"], list(rows))


@app.get("/api/trips/{trip_id}")
async def get_trip(trip_id: UUID, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    return await serialize_trip(database, user["id"], trip)


@app.delete("/api/trips/{trip_id}")
async def delete_roster(trip_id: UUID, user: UserDep, database: DbDep) -> dict[str, str]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    if trip["created_by"] != UUID(user["id"]):
        creator = await database.fetchval("SELECT created_by FROM carpool_groups WHERE id = $1", trip["group_id"])
        if creator != UUID(user["id"]):
            raise HTTPException(status_code=403, detail="Only the roster creator can delete an unaccepted roster")
    if trip["status"] != "planned":
        raise HTTPException(status_code=409, detail="Only planned, unaccepted rosters can be deleted")
    accepted = await database.fetchval(
        "SELECT 1 FROM trip_responses WHERE trip_id = $1 AND status = 'accepted' LIMIT 1",
        trip_id,
    )
    if accepted:
        raise HTTPException(status_code=409, detail="Accepted rosters cannot be deleted")
    async with database.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE trips
                SET status = 'ignored',
                    cancellation_reason = 'Roster deleted before driver acceptance'
                WHERE id = $1 AND status = 'planned'
                RETURNING *
                """,
                trip_id,
            )
            if not updated:
                raise HTTPException(status_code=409, detail="Roster can no longer be deleted")
            await conn.execute(
                "UPDATE roster_change_requests SET status = 'cancelled' WHERE trip_id = $1 AND status = 'open'",
                trip_id,
            )
            await conn.execute(
                """
                UPDATE notifications
                SET read_at = COALESCE(read_at, now())
                WHERE entity_type = 'trip' AND entity_id = $1 AND read_at IS NULL
                """,
                trip_id,
            )
            await conn.execute(
                """
                INSERT INTO audit_records (actor_user_id, group_id, trip_id, action, details)
                VALUES ($1, $2, $3, 'trip.deleted', $4::jsonb)
                """,
                UUID(user["id"]),
                trip["group_id"],
                trip_id,
                json.dumps({"status": "ignored", "reason": "deleted_before_acceptance"}),
            )
    return {"status": "deleted"}


@app.post("/api/trips/{trip_id}/responses")
async def respond_trip(trip_id: UUID, payload: TripResponseIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    if trip["driver_user_id"] != UUID(user["id"]):
        raise HTTPException(status_code=403, detail="Only the assigned driver can accept or decline")
    row = await database.fetchrow(
        """
        INSERT INTO trip_responses (trip_id, user_id, status, note)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (trip_id, user_id) DO NOTHING
        RETURNING *
        """,
        trip_id,
        UUID(user["id"]),
        payload.status,
        payload.note,
    )
    if not row:
        raise HTTPException(status_code=409, detail="Trip assignment already decided")
    await notify(database, trip["created_by"], "roster_response", "Roster response", f"{user['name']} {payload.status} a roster assignment.", "trip", trip_id)
    await audit(database, user["id"], "trip.response", trip["group_id"], trip_id, {"status": payload.status})
    return row_to_dict(row) or {}


@app.get("/api/trips/{trip_id}/eligible-swap-drivers")
async def eligible_swap_drivers(trip_id: UUID, user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    rows = await database.fetch(
        """
        SELECT u.id, u.name, u.email, u.phone
        FROM group_members gm
        JOIN users u ON u.id = gm.user_id
        WHERE gm.group_id = $1
          AND gm.status = 'active'
          AND gm.user_id <> $2
          AND NOT EXISTS (
              SELECT 1
              FROM trip_children tc
              LEFT JOIN driver_approvals da
                ON da.group_id = $1
               AND da.child_id = tc.child_id
               AND da.driver_user_id = gm.user_id
               AND da.approved = true
              WHERE tc.trip_id = $3
                AND da.id IS NULL
          )
        ORDER BY u.name
        """,
        trip["group_id"],
        trip["driver_user_id"],
        trip_id,
    )
    return rows_to_list(rows)


@app.post("/api/trips/{trip_id}/change-requests")
async def request_roster_change(trip_id: UUID, payload: ChangeRequestIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    if payload.request_type == "swap":
        if trip["driver_user_id"] != UUID(user["id"]):
            raise HTTPException(status_code=403, detail="Only the assigned driver can request a swap")
        if trip["status"] not in {"planned", "delayed"}:
            raise HTTPException(status_code=409, detail="Swap requests are only available before a trip starts")
        if not payload.proposed_driver_user_id:
            raise HTTPException(status_code=400, detail="Choose a proposed replacement driver")
        if payload.proposed_driver_user_id == trip["driver_user_id"]:
            raise HTTPException(status_code=400, detail="Choose a different replacement driver")
        existing_open_swap = await database.fetchval(
            """
            SELECT 1
            FROM roster_change_requests
            WHERE trip_id = $1 AND request_type = 'swap' AND status = 'open'
            LIMIT 1
            """,
            trip_id,
        )
        if existing_open_swap:
            raise HTTPException(status_code=409, detail="A swap request is already open for this trip")
    if payload.proposed_driver_user_id:
        await require_group_member(database, payload.proposed_driver_user_id, trip["group_id"])
        missing_approval = await database.fetchval(
            """
            SELECT 1
            FROM trip_children tc
            LEFT JOIN driver_approvals da
              ON da.group_id = $1
             AND da.child_id = tc.child_id
             AND da.driver_user_id = $2
             AND da.approved = true
            WHERE tc.trip_id = $3
              AND da.id IS NULL
            LIMIT 1
            """,
            trip["group_id"],
            payload.proposed_driver_user_id,
            trip_id,
        )
        if missing_approval:
            raise HTTPException(status_code=403, detail="Proposed driver is not approved for every child on this trip")
    row = await database.fetchrow(
        """
        INSERT INTO roster_change_requests (trip_id, requested_by, request_type, proposed_driver_user_id, note)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        trip_id,
        UUID(user["id"]),
        payload.request_type,
        payload.proposed_driver_user_id,
        payload.note,
    )
    request_id = row["id"]
    if trip["created_by"] != UUID(user["id"]):
        await notify(
            database,
            trip["created_by"],
            "trip_swap_request",
            "Trip swap requested",
            f"{user['name']} requested a swap for {trip_label(trip)}. {payload.note}",
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"swap_request:{request_id}:organiser:{trip['created_by']}",
            action_url=f"/?tab=trip&trip={trip_id}",
        )
    if payload.proposed_driver_user_id:
        await notify(
            database,
            payload.proposed_driver_user_id,
            "trip_swap_request",
            "Trip swap request received",
            f"{user['name']} asked you to cover {trip_label(trip)}. Review and confirm before anything changes.",
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"swap_request:{request_id}:driver:{payload.proposed_driver_user_id}",
            action_url=f"/?tab=trip&trip={trip_id}&swap_request={request_id}",
    )
    await audit(
        database,
        user["id"],
        "trip.change_requested",
        trip["group_id"],
        trip_id,
        {"request_type": payload.request_type, "proposed_driver_user_id": str(payload.proposed_driver_user_id) if payload.proposed_driver_user_id else None, "request_id": str(request_id)},
    )
    return row_to_dict(row) or {}


@app.get("/api/trips/{trip_id}/change-requests")
async def list_trip_change_requests(trip_id: UUID, user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    rows = await database.fetch(
        """
        SELECT rcr.*, requester.name AS requester_name, proposed.name AS proposed_driver_name
        FROM roster_change_requests rcr
        JOIN users requester ON requester.id = rcr.requested_by
        LEFT JOIN users proposed ON proposed.id = rcr.proposed_driver_user_id
        WHERE rcr.trip_id = $1
          AND (rcr.requested_by = $2 OR rcr.proposed_driver_user_id = $2 OR $2 = $3)
        ORDER BY rcr.created_at DESC
        """,
        trip_id,
        UUID(user["id"]),
        trip["created_by"],
    )
    return rows_to_list(rows)


@app.post("/api/roster-change-requests/{request_id}/decision")
async def decide_roster_change(request_id: UUID, payload: ChangeRequestDecisionIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    request = await database.fetchrow(
        """
        SELECT rcr.*, t.group_id, t.driver_user_id, t.service_date, t.trip_type, t.expected_time, t.created_by
        FROM roster_change_requests rcr
        JOIN trips t ON t.id = rcr.trip_id
        WHERE rcr.id = $1
        """,
        request_id,
    )
    if not request:
        raise HTTPException(status_code=404, detail="Swap request not found")
    await require_group_member(database, user["id"], request["group_id"])
    if request["request_type"] == "swap" and request["proposed_driver_user_id"] != UUID(user["id"]):
        raise HTTPException(status_code=403, detail="Only the proposed replacement driver can decide this swap")
    if request["status"] != "open":
        raise HTTPException(status_code=409, detail="Swap request already decided")
    async with database.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE roster_change_requests
                SET status = $2, note = COALESCE($3, note)
                WHERE id = $1 AND status = 'open'
                RETURNING *
                """,
                request_id,
                payload.status,
                payload.note,
            )
            if not row:
                raise HTTPException(status_code=409, detail="Swap request already decided")
            if payload.status == "accepted" and request["request_type"] == "swap" and request["proposed_driver_user_id"]:
                await conn.execute(
                    "UPDATE trips SET driver_user_id = $2 WHERE id = $1",
                    request["trip_id"],
                    request["proposed_driver_user_id"],
                )
                await conn.execute(
                    """
                    UPDATE roster_change_requests
                    SET status = 'cancelled'
                    WHERE trip_id = $1 AND id <> $2 AND status = 'open'
                    """,
                    request["trip_id"],
                    request_id,
                )
                await conn.execute(
                    """
                    INSERT INTO trip_responses (trip_id, user_id, status, note)
                    VALUES ($1, $2, 'accepted', $3)
                    ON CONFLICT (trip_id, user_id)
                    DO UPDATE SET status = 'accepted', note = EXCLUDED.note, created_at = now()
                    """,
                    request["trip_id"],
                    request["proposed_driver_user_id"],
                    payload.note or "Accepted trip swap request.",
                )
    recipients = {request["requested_by"], request["created_by"]}
    for recipient_id in recipients:
        await notify(
            database,
            recipient_id,
            "trip_swap_decision",
            f"Trip swap {payload.status}",
            f"The swap request for {trip_label(request)} was {payload.status}.",
            "trip",
            request["trip_id"],
            email=True,
            idempotency_key=f"swap_decision:{request_id}:{payload.status}:{recipient_id}",
            action_url=f"/?tab=trip&trip={request['trip_id']}",
        )
    await audit(
        database,
        user["id"],
        "trip.change_decided",
        request["group_id"],
        request["trip_id"],
        {"request_type": request["request_type"], "request_id": str(request_id), "status": payload.status},
    )
    return row_to_dict(row) or {}


@app.post("/api/trips/{trip_id}/start")
async def start_trip(trip_id: UUID, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    if trip["status"] not in {"planned", "delayed"}:
        raise HTTPException(status_code=409, detail="Trip cannot be started from its current status")
    if trip["service_date"] != date.today():
        raise HTTPException(status_code=409, detail="Trip can only be started on its service date")
    accepted = await database.fetchval(
        """
        SELECT 1
        FROM trip_responses
        WHERE trip_id = $1 AND user_id = $2 AND status = 'accepted'
        """,
        trip_id,
        UUID(user["id"]),
    )
    if not accepted:
        raise HTTPException(status_code=409, detail="Accept this trip before starting it")
    updated = await database.fetchrow(
        """
        UPDATE trips
        SET status = 'started',
            started_at = now(),
            ended_at = NULL,
            safety_timeout_at = now() + ($2::text || ' minutes')::interval
        WHERE id = $1
        RETURNING *
        """,
        trip_id,
        str(settings.safety_timeout_minutes),
    )
    parent_ids = await database.fetch(
        "SELECT DISTINCT c.parent_id FROM trip_children tc JOIN children c ON c.id = tc.child_id WHERE tc.trip_id = $1",
        trip_id,
    )
    for parent in parent_ids:
        await notify(
            database,
            parent["parent_id"],
            "trip_start",
            "Trip started",
            f"The assigned driver started {trip_label(updated)}.",
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"trip_start:{trip_id}:{parent['parent_id']}",
            action_url=f"/?tab=trip&trip={trip_id}",
        )
    await audit(database, user["id"], "trip.started", trip["group_id"], trip_id)
    return await serialize_trip(database, user["id"], updated)


@app.post("/api/trips/{trip_id}/end")
async def end_trip(trip_id: UUID, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    updated = await database.fetchrow(
        "UPDATE trips SET status = 'completed', ended_at = now() WHERE id = $1 RETURNING *",
        trip_id,
    )
    parent_ids = await database.fetch(
        "SELECT DISTINCT c.parent_id FROM trip_children tc JOIN children c ON c.id = tc.child_id WHERE tc.trip_id = $1",
        trip_id,
    )
    for parent in parent_ids:
        await notify(
            database,
            parent["parent_id"],
            "trip_end",
            "Trip ended",
            f"Location sharing has stopped for {trip_label(updated)}.",
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"trip_end:{trip_id}:{parent['parent_id']}",
            action_url=f"/?tab=trip&trip={trip_id}",
        )
    await audit(database, user["id"], "trip.ended", trip["group_id"], trip_id)
    return await serialize_trip(database, user["id"], updated)


@app.post("/api/trips/{trip_id}/cancel")
async def cancel_trip(trip_id: UUID, payload: TripNoteIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    updated = await database.fetchrow(
        "UPDATE trips SET status = 'cancelled', cancellation_reason = $2, ended_at = now() WHERE id = $1 RETURNING *",
        trip_id,
        payload.reason or payload.note,
    )
    for recipient_id in await affected_trip_user_ids(database, trip):
        await notify(
            database,
            recipient_id,
            "trip_cancelled",
            "Trip cancelled",
            payload.reason or f"{trip_label(updated)} was cancelled.",
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"trip_cancelled:{trip_id}:{recipient_id}",
            action_url=f"/?tab=trip&trip={trip_id}",
        )
    await audit(database, user["id"], "trip.cancelled", trip["group_id"], trip_id)
    return await serialize_trip(database, user["id"], updated)


@app.post("/api/trips/{trip_id}/delay")
async def delay_trip(trip_id: UUID, payload: TripNoteIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    if trip["status"] not in {"planned", "delayed", "started"} or trip["service_date"] < date.today():
        raise HTTPException(status_code=409, detail="Delay can only be reported for an active or upcoming trip")
    minutes = payload.minutes or 10
    updated = await database.fetchrow(
        "UPDATE trips SET status = 'delayed', delay_minutes = $2, delay_note = $3 WHERE id = $1 RETURNING *",
        trip_id,
        minutes,
        payload.note or f"Driver reported a {minutes}-minute delay.",
    )
    body = payload.note or f"Driver reported a {minutes}-minute delay for {trip_label(updated)}."
    for recipient_id in await affected_trip_user_ids(database, updated, include_driver=False):
        await notify(
            database,
            recipient_id,
            "delay",
            "Trip delayed",
            body,
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"trip_delay:{trip_id}:{minutes}:{recipient_id}",
            action_url=f"/?tab=trip&trip={trip_id}",
        )
    await audit(database, user["id"], "trip.delayed", trip["group_id"], trip_id, {"minutes": minutes})
    return await serialize_trip(database, user["id"], updated)


@app.post("/api/trips/{trip_id}/driver-unavailable")
async def driver_unavailable(trip_id: UUID, payload: TripNoteIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    updated = await database.fetchrow("UPDATE trips SET status = 'driver_unavailable', delay_note = $2 WHERE id = $1 RETURNING *", trip_id, payload.note)
    for recipient_id in await affected_trip_user_ids(database, updated):
        await notify(
            database,
            recipient_id,
            "driver_unavailable",
            "Driver unavailable",
            payload.note or f"The assigned driver is unavailable for {trip_label(updated)}.",
            "trip",
            trip_id,
            email=True,
            idempotency_key=f"driver_unavailable:{trip_id}:{recipient_id}",
            action_url=f"/?tab=trip&trip={trip_id}",
        )
    await audit(database, user["id"], "trip.driver_unavailable", trip["group_id"], trip_id)
    return await serialize_trip(database, user["id"], updated)


@app.post("/api/trips/{trip_id}/children/{child_id}/handover")
async def handover(trip_id: UUID, child_id: UUID, payload: HandoverIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    linked = await database.fetchval("SELECT 1 FROM trip_children WHERE trip_id = $1 AND child_id = $2", trip_id, child_id)
    if not linked:
        raise HTTPException(status_code=404, detail="Child is not assigned to this trip")
    status_column = "pickup_status" if payload.handover_type in {"picked_up", "absent"} else "dropoff_status"
    value = "absent" if payload.handover_type == "absent" else payload.handover_type
    async with database.acquire() as conn:
        async with conn.transaction():
            await conn.execute(f"UPDATE trip_children SET {status_column} = $3 WHERE trip_id = $1 AND child_id = $2", trip_id, child_id, value)
            row = await conn.fetchrow(
                """
                INSERT INTO handovers (trip_id, child_id, handover_type, completed_by, note)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (trip_id, child_id, handover_type)
                DO UPDATE SET note = COALESCE(handovers.note, EXCLUDED.note)
                RETURNING *
                """,
                trip_id,
                child_id,
                payload.handover_type,
                UUID(user["id"]),
                payload.note,
            )
    parent = await database.fetchrow("SELECT parent_id, name FROM children WHERE id = $1", child_id)
    await notify(
        database,
        parent["parent_id"],
        "handover",
        f"{parent['name']} {payload.handover_type.replace('_', ' ')}",
        payload.note or "A handover update was recorded.",
        "trip",
        trip_id,
        email=True,
        idempotency_key=f"handover:{trip_id}:{child_id}:{payload.handover_type}",
        action_url=f"/?tab=trip&trip={trip_id}",
    )
    await audit(database, user["id"], "trip.handover", trip["group_id"], trip_id, {"child_id": str(child_id), "handover_type": payload.handover_type})
    return row_to_dict(row) or {}


@app.post("/api/trips/{trip_id}/locations")
async def post_location(trip_id: UUID, payload: LocationIn, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await require_trip_driver(database, user["id"], trip_id)
    if trip["status"] != "started" or trip["ended_at"] is not None:
        raise HTTPException(status_code=409, detail="Location is accepted only during an active trip")
    if trip["safety_timeout_at"] and datetime.now(UTC) > trip["safety_timeout_at"]:
        await database.execute("UPDATE trips SET status = 'completed', ended_at = now() WHERE id = $1", trip_id)
        raise HTTPException(status_code=409, detail="Safety timeout reached; location sharing stopped")
    recorded_at = payload.recorded_at or datetime.now(UTC)
    row = await database.fetchrow(
        """
        INSERT INTO trip_locations (trip_id, driver_user_id, latitude, longitude, accuracy_meters, speed_mps, heading_degrees, recorded_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        trip_id,
        UUID(user["id"]),
        payload.latitude,
        payload.longitude,
        payload.accuracy_meters,
        payload.speed_mps,
        payload.heading_degrees,
        recorded_at,
    )
    await database.execute(
        "DELETE FROM trip_locations WHERE received_at < now() - ($1::text || ' minutes')::interval",
        str(settings.raw_location_retention_minutes),
    )
    return row_to_dict(row) or {}


@app.get("/api/trips/{trip_id}/location")
async def latest_location(trip_id: UUID, user: UserDep, database: DbDep) -> dict[str, Any]:
    await trip_with_permission(database, user["id"], trip_id)
    row = await database.fetchrow("SELECT * FROM trip_locations WHERE trip_id = $1 ORDER BY received_at DESC LIMIT 1", trip_id)
    if not row:
        return {"location": None, "fresh": False, "message": "GPS unavailable"}
    payload = row_to_dict(row) or {}
    age = (datetime.now(UTC) - row["received_at"]).total_seconds()
    payload["age_seconds"] = int(age)
    payload["fresh"] = age <= settings.location_stale_seconds
    payload["message"] = f"Updated {int(age)} seconds ago" if payload["fresh"] else f"GPS stale: last updated {int(age)} seconds ago"
    return {"location": payload}


@app.get("/api/trips/{trip_id}/route")
async def trip_route(trip_id: UUID, user: UserDep, database: DbDep) -> dict[str, Any]:
    trip = await trip_with_permission(database, user["id"], trip_id)
    if trip["status"] != "started" or trip["ended_at"] is not None:
        return route_unavailable("trip_not_active", "Live ETA is available only during an active trip")
    latest = await database.fetchrow("SELECT * FROM trip_locations WHERE trip_id = $1 ORDER BY received_at DESC LIMIT 1", trip_id)
    if not latest:
        return route_unavailable("location_missing", "Waiting for driver's location...")
    age = (datetime.now(UTC) - latest["received_at"]).total_seconds()
    if age > settings.location_stale_seconds:
        return route_unavailable(
            "location_stale",
            "Waiting for updated driver location...",
            location_age_seconds=int(age),
        )
    school = await database.fetchrow(
        """
        SELECT s.name, s.address, s.city, s.country
        FROM carpool_groups cg
        JOIN schools s ON s.id = cg.school_id
        WHERE cg.id = $1
        """,
        trip["group_id"],
    )
    if not school:
        return route_unavailable("destination_missing", "Destination unavailable")
    destination = ", ".join(
        part for part in [school["address"], school["city"], school["country"]] if part
    ) or ", ".join(part for part in [school["name"], school["city"], school["country"]] if part)
    if not destination and trip["trip_type"] == "pickup":
        return route_unavailable("destination_missing", "Destination unavailable")
    if not settings.google_routes_api_key:
        logger.warning("route compute unavailable", extra={"trip_id": str(trip_id), "reason": "routing_unavailable", "google_status": None})
        return route_unavailable("routing_unavailable", "ETA temporarily unavailable")

    request_body = {
        "origin": {
            "location": {
                "latLng": {
                    "latitude": float(latest["latitude"]),
                    "longitude": float(latest["longitude"]),
                }
            }
        },
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
        "computeAlternativeRoutes": False,
        "units": "METRIC",
    }
    stops = []
    full_trip_route = trip["trip_type"] == "pickup"
    target_label = "school"
    route_destination = destination
    if trip["trip_type"] == "pickup":
        request_body["destination"] = {"address": destination}
    else:
        child_rows, full_trip_route = await trip_route_children_for_user(database, user["id"], trip)
        if not child_rows:
            return route_unavailable("destination_missing", "Destination unavailable")
        pending_child_rows = [child for child in child_rows if child["dropoff_status"] == "pending"]
        missing_home = []
        for child in pending_child_rows:
            waypoint = route_waypoint_from_child(child)
            if not waypoint:
                missing_home.append(child["name"])
                continue
            stops.append({
                "child_id": str(child["id"]),
                "name": child["name"],
                "address": child_home_address(child),
                "waypoint": waypoint,
            })
        if missing_home:
            return route_unavailable("destination_missing", "Destination unavailable")
        if not stops:
            return route_unavailable("trip_not_active", "Live ETA is available only during an active trip")
        target_label = "last home stop"
        route_destination = stops[-1]["address"]
        request_body["destination"] = stops[-1]["waypoint"]
        if len(stops) > 1:
            request_body["intermediates"] = [stop["waypoint"] for stop in stops[:-1]]
        if not full_trip_route:
            target_label = "your drop-off stop"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers={
                    "Content-Type": "application/json",
                    "X-Goog-Api-Key": settings.google_routes_api_key,
                    "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
                },
                json=request_body,
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        google_error = safe_google_error(response)
        logger.warning(
            "route compute failed",
            extra={
                "trip_id": str(trip_id),
                "reason": "routing_unavailable",
                "google_http_status": getattr(response, "status_code", None),
                "google_error_status": google_error.get("status"),
                "google_error_code": google_error.get("code"),
            },
        )
        return route_unavailable("routing_unavailable", "ETA temporarily unavailable")

    routes = response.json().get("routes", [])
    if not routes:
        logger.warning(
            "route compute returned no routes",
            extra={"trip_id": str(trip_id), "reason": "routing_unavailable", "google_http_status": getattr(response, "status_code", None)},
        )
        return route_unavailable("routing_unavailable", "ETA temporarily unavailable")
    route = routes[0]
    duration_seconds = parse_google_duration_seconds(route.get("duration"))
    distance_meters = parse_google_distance_meters(route.get("distanceMeters"))
    if duration_seconds is None or distance_meters is None:
        logger.warning(
            "route compute missing required fields",
            extra={
                "trip_id": str(trip_id),
                "reason": "routing_unavailable",
                "google_http_status": getattr(response, "status_code", None),
                "has_duration": duration_seconds is not None,
                "has_distance_meters": distance_meters is not None,
            },
        )
        return route_unavailable("routing_unavailable", "ETA temporarily unavailable")
    logger.info(
        "route compute succeeded",
        extra={
            "trip_id": str(trip_id),
            "reason": None,
            "google_http_status": getattr(response, "status_code", None),
            "has_duration": True,
            "has_distance_meters": True,
        },
    )
    return {
        "route": {
            "destination": route_destination,
            "target_label": target_label,
            "scope": "whole_trip" if full_trip_route else "your_child",
            "trip_type": trip["trip_type"],
            "stops": [{key: value for key, value in stop.items() if key != "waypoint"} for stop in stops],
            "duration": route.get("duration"),
            "duration_seconds": duration_seconds,
            "eta_at": (datetime.now(UTC) + timedelta(seconds=duration_seconds)).isoformat(),
            "distance_meters": distance_meters,
            "encoded_polyline": route.get("polyline", {}).get("encodedPolyline"),
            "location_age_seconds": int(age),
        }
    }


@app.websocket("/api/trips/{trip_id}/location-stream")
async def location_stream(websocket: WebSocket, trip_id: UUID, token: str):
    await websocket.accept()
    database = await db()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id = UUID(payload["sub"])
        await trip_with_permission(database, user_id, trip_id)
        last_id = None
        while True:
            row = await database.fetchrow("SELECT * FROM trip_locations WHERE trip_id = $1 ORDER BY received_at DESC LIMIT 1", trip_id)
            if row and row["id"] != last_id:
                last_id = row["id"]
                await websocket.send_json(row_to_dict(row))
            await asyncio.sleep(5)
    except (WebSocketDisconnect, jwt.PyJWTError, HTTPException):
        await websocket.close()


@app.get("/api/notifications")
async def list_notifications(user: UserDep, database: DbDep) -> list[dict[str, Any]]:
    rows = await database.fetch(
        "SELECT * FROM notifications WHERE user_id = $1 ORDER BY created_at DESC LIMIT 100",
        UUID(user["id"]),
    )
    return rows_to_list(rows)


@app.post("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: UUID, user: UserDep, database: DbDep) -> dict[str, str]:
    await database.execute(
        "UPDATE notifications SET read_at = now() WHERE id = $1 AND user_id = $2",
        notification_id,
        UUID(user["id"]),
    )
    return {"status": "ok"}


@app.delete("/api/notifications/read")
async def delete_read_notifications(user: UserDep, database: DbDep) -> dict[str, str]:
    await database.execute(
        "DELETE FROM notifications WHERE user_id = $1 AND read_at IS NOT NULL",
        UUID(user["id"]),
    )
    return {"status": "deleted"}


@app.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: UUID, user: UserDep, database: DbDep) -> dict[str, str]:
    await database.execute(
        "DELETE FROM notifications WHERE id = $1 AND user_id = $2",
        notification_id,
        UUID(user["id"]),
    )
    return {"status": "deleted"}


@app.get("/api/audit")
async def audit_log(user: UserDep, database: DbDep, group_id: UUID | None = None) -> list[dict[str, Any]]:
    if group_id:
        await require_group_member(database, user["id"], group_id)
        rows = await database.fetch("SELECT * FROM audit_records WHERE group_id = $1 ORDER BY created_at DESC LIMIT 100", group_id)
    else:
        rows = await database.fetch(
            """
            SELECT ar.*
            FROM audit_records ar
            JOIN group_members gm ON gm.group_id = ar.group_id AND gm.user_id = $1 AND gm.status = 'active'
            ORDER BY ar.created_at DESC
            LIMIT 100
            """,
            UUID(user["id"]),
        )
    return rows_to_list(rows)
