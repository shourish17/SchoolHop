"""Optional real PostgreSQL tests, restricted to an isolated temporary Unix socket.

Set SCHOOLHOP_TEST_PG_SOCKET to the socket of a disposable PostgreSQL 17 container
created for these tests. Never accepts a production DSN, TCP host, or database name.
All Firebase/APNs HTTP clients and credentials are mocked.
"""
import asyncio
import os
import unittest
from datetime import UTC, datetime, time
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import HTTPException

import app.main as main

SOCKET = os.environ.get("SCHOOLHOP_TEST_PG_SOCKET")
REAL_SLEEP = asyncio.sleep
SCHEMA = """
CREATE TABLE mobile_devices (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL,
    provider text NOT NULL, token text NOT NULL, platform text, enabled boolean NOT NULL DEFAULT true,
    last_seen_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, token)
);
CREATE TABLE trips (id uuid PRIMARY KEY);
CREATE TABLE roster_change_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), trip_id uuid NOT NULL REFERENCES trips(id),
    requested_by uuid NOT NULL, request_type text NOT NULL, proposed_driver_user_id uuid,
    note text NOT NULL, status text NOT NULL DEFAULT 'open', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE trip_children (trip_id uuid, child_id uuid);
CREATE TABLE driver_approvals (id uuid, group_id uuid, child_id uuid, driver_user_id uuid, approved boolean);
CREATE TABLE notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid, kind text, title text, body text,
    entity_type text, entity_id uuid, idempotency_key text, action_url text
);
CREATE UNIQUE INDEX notifications_idempotency ON notifications (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE audit_records (actor_user_id uuid, group_id uuid, trip_id uuid, action text, details jsonb);
"""


@unittest.skipUnless(SOCKET, "requires disposable PostgreSQL Unix socket")
class PostgresNotificationConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        socket = Path(SOCKET).resolve()
        if socket.name != "socket" or not socket.parent.name.startswith("schoolhop-fcm-concurrency-pg-") or socket.parent.parent != Path("/tmp"):
            raise ValueError("Refusing a non-test PostgreSQL socket")
        options = dict(host=str(socket), user="postgres", database="schoolhop_fcm_concurrency_test",
                       server_settings={"application_name": "schoolhop_concurrency_test"})
        self.admin = await asyncpg.connect(**options)
        self.schema = "test_" + uuid4().hex
        await self.admin.execute(f'CREATE SCHEMA "{self.schema}"')
        options["server_settings"]["search_path"] = self.schema
        self.database = await asyncpg.create_pool(**options, min_size=1, max_size=8)
        await self.database.execute(SCHEMA)
        self.tasks = []
        self.owner, self.organiser, self.replacement, self.other_replacement = [uuid4() for _ in range(4)]
        self.device_id, self.trip_id, self.group_id = uuid4(), uuid4(), uuid4()
        await self.database.execute("INSERT INTO trips VALUES ($1)", self.trip_id)
        await self.database.execute("INSERT INTO mobile_devices (id, user_id, provider, token, platform) VALUES ($1,$2,'fcm','synthetic-test-device','android')", self.device_id, self.owner)
        for user_id in [self.organiser, self.replacement, self.other_replacement]:
            await self.database.execute("INSERT INTO mobile_devices (user_id,provider,token,platform) VALUES ($1,'fcm',$2,'android')", user_id, "synthetic-" + user_id.hex)
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.client.post.return_value = self.response(200)
        for patcher in [patch.object(main.settings, "fcm_service_account_path", "/fake/test-only.json"),
                        patch.object(main, "fcm_access_token", new=AsyncMock(return_value="synthetic-oauth-only")),
                        patch.object(main.httpx, "AsyncClient", return_value=self.client),
                        patch.object(main.secrets, "randbelow", return_value=0)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.database.close()
        await self.admin.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
        await self.admin.close()

    def task(self, awaitable):
        task = asyncio.create_task(awaitable)
        self.tasks.append(task)
        return task

    def response(self, status):
        return httpx.Response(status, request=httpx.Request("POST", "https://fcm.googleapis.com/send"))

    async def wait_for_blocked_queries(self, fragment, count):
        async with asyncio.timeout(5):
            while True:
                blocked = await self.admin.fetchval("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND application_name='schoolhop_concurrency_test' AND wait_event_type='Lock' AND strpos(query,$1)>0", fragment)
                if blocked >= count:
                    return
                await REAL_SLEEP(0.005)

    async def deliver(self):
        await main.deliver_push(self.database, self.owner, "handover", "Synthetic child", "Synthetic note", "trip", self.trip_id)

    async def mutate_registration(self, kind):
        payload = main.MobileDeviceIn(provider="fcm", token="synthetic-test-device", platform="android")
        user = {"id": str(self.owner if kind != "reassignment" else self.replacement)}
        if kind == "logout":
            await main.unregister_mobile_device(payload, user, self.database)
        else:
            await main.register_mobile_device(payload, user, self.database)

    async def test_original_registration_reproduction_no_retry_after_reassignment_logout_or_refresh(self):
        for kind in ["reassignment", "logout", "refresh"]:
            with self.subTest(kind=kind):
                await self.database.execute("UPDATE mobile_devices SET user_id=$2,enabled=true,last_seen_at=now() WHERE id=$1", self.device_id, self.owner)
                entered, release = asyncio.Event(), asyncio.Event()
                self.client.post.reset_mock()
                async def post(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return self.response(503)
                self.client.post.side_effect = post
                writer = None
                async def backoff(*args):
                    # Wait for the real UPDATE/UPSERT to commit before retrying.
                    await writer
                with patch.object(main.asyncio, "sleep", side_effect=backoff):
                    delivery = self.task(self.deliver())
                    await asyncio.wait_for(entered.wait(), 5)
                    writer = self.task(self.mutate_registration(kind))
                    fragment = "UPDATE mobile_devices" if kind == "logout" else "INSERT INTO mobile_devices"
                    await self.wait_for_blocked_queries(fragment, 1)
                    self.assertFalse(writer.done(), "Registration mutation must wait for in-flight attempt")
                    release.set()
                    await asyncio.wait_for(asyncio.gather(delivery, writer), 5)
                self.client.post.assert_awaited_once()

    async def test_guard_waiting_for_row_lock_is_inside_device_deadline(self):
        async with self.database.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE mobile_devices SET enabled=enabled WHERE id=$1", self.device_id)
                with patch.object(main, "FCM_DELIVERY_TIMEOUT_SECONDS", 0.02):
                    await asyncio.wait_for(self.deliver(), 2)
        self.client.post.assert_not_awaited()

    async def test_mutation_winning_row_lock_prevents_first_send(self):
        for kind in ["reassignment", "logout", "refresh"]:
            with self.subTest(kind=kind):
                await self.database.execute("UPDATE mobile_devices SET user_id=$2,enabled=true,last_seen_at=now() WHERE id=$1", self.device_id, self.owner)
                async with self.database.acquire() as writer:
                    async with writer.transaction():
                        await writer.execute("UPDATE mobile_devices SET user_id=$2,enabled=$3,last_seen_at=now() WHERE id=$1", self.device_id, self.replacement if kind == "reassignment" else self.owner, kind != "logout")
                        delivery = self.task(self.deliver())
                        await self.wait_for_blocked_queries("SELECT id FROM mobile_devices", 1)
                await asyncio.wait_for(delivery, 2)
                self.client.post.assert_not_awaited()

    async def test_cleanup_does_not_disable_refreshed_or_reassigned_row(self):
        for kind in ["refresh", "reassignment"]:
            with self.subTest(kind=kind):
                await self.database.execute("UPDATE mobile_devices SET user_id=$2,enabled=true,last_seen_at=now() WHERE id=$1", self.device_id, self.owner)
                async def fail_old_snapshot(*args, **kwargs):
                    await self.mutate_registration(kind)
                    response = httpx.Response(404, json={"error": {"details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": "UNREGISTERED"}]}}, request=httpx.Request("POST", "https://fcm.googleapis.com/send"))
                    raise httpx.HTTPStatusError("synthetic failure", request=response.request, response=response)
                with patch.object(main, "send_push_to_device", side_effect=fail_old_snapshot):
                    await self.deliver()
                self.assertTrue(await self.database.fetchval("SELECT enabled FROM mobile_devices WHERE id=$1", self.device_id))

    def trip(self, trip_id):
        return {"id": trip_id, "group_id": self.group_id, "driver_user_id": self.owner, "created_by": self.organiser,
                "status": "planned", "service_date": datetime.now(UTC).date(), "trip_type": "pickup", "expected_time": time(8)}

    async def request_swap(self, replacement=None, trip_id=None):
        return await main.request_roster_change(trip_id or self.trip_id,
            main.ChangeRequestIn(request_type="swap", proposed_driver_user_id=replacement or self.replacement, note="Synthetic review request"),
            {"id": str(self.owner), "name": "Synthetic driver"}, self.database)

    async def test_original_overlapping_swap_reproduction_one_winner_one_conflict_two_sends(self):
        for different_replacement in [False, True]:
            with self.subTest(different_replacement=different_replacement):
                await self.database.execute("DELETE FROM roster_change_requests; DELETE FROM notifications")
                with patch.object(main, "trip_with_permission", side_effect=lambda db, user, trip_id: self.trip(trip_id)), patch.object(main, "require_group_member", new=AsyncMock()), patch.object(main, "send_push_to_device", new=AsyncMock()) as send:
                    async with self.database.acquire() as blocker:
                        async with blocker.transaction():
                            await blocker.fetchrow("SELECT id FROM trips WHERE id=$1 FOR UPDATE", self.trip_id)
                            first = self.task(self.request_swap())
                            second = self.task(self.request_swap(self.other_replacement if different_replacement else self.replacement))
                            await self.wait_for_blocked_queries("SELECT id FROM trips WHERE id = $1 FOR UPDATE", 2)
                    results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 5)
                winners = [row for row in results if isinstance(row, dict)]
                conflicts = [error for error in results if isinstance(error, HTTPException) and error.status_code == 409]
                self.assertEqual((len(winners), len(conflicts)), (1, 1), results)
                self.assertEqual(await self.database.fetchval("SELECT count(*) FROM roster_change_requests WHERE status='open' AND request_type='swap'"), 1)
                self.assertEqual(send.await_count, 2)
                self.assertEqual({call.args[0]["user_id"] for call in send.await_args_list}, {self.organiser, UUID(winners[0]["proposed_driver_user_id"])})
                self.assertEqual(await self.database.fetchval("SELECT count(*) FROM notifications"), 2)

    async def test_different_trips_do_not_share_swap_lock(self):
        other_trip = uuid4()
        await self.database.execute("INSERT INTO trips VALUES ($1)", other_trip)
        with patch.object(main, "trip_with_permission", side_effect=lambda db, user, trip_id: self.trip(trip_id)), patch.object(main, "require_group_member", new=AsyncMock()), patch.object(main, "send_push_to_device", new=AsyncMock()) as send:
            async with self.database.acquire() as blocker:
                async with blocker.transaction():
                    await blocker.fetchrow("SELECT id FROM trips WHERE id=$1 FOR UPDATE", self.trip_id)
                    waiting = self.task(self.request_swap())
                    await self.wait_for_blocked_queries("SELECT id FROM trips WHERE id = $1 FOR UPDATE", 1)
                    result = await asyncio.wait_for(self.request_swap(trip_id=other_trip), 2)
                    self.assertEqual(result["trip_id"], str(other_trip))
                    self.assertFalse(waiting.done())
            await asyncio.wait_for(waiting, 2)
        self.assertEqual(send.await_count, 4)

    async def test_closed_swap_does_not_block_new_request(self):
        await self.database.execute("INSERT INTO roster_change_requests (trip_id,requested_by,request_type,note,status) VALUES ($1,$2,'swap','synthetic','declined')", self.trip_id, self.owner)
        with patch.object(main, "trip_with_permission", side_effect=lambda db, user, trip_id: self.trip(trip_id)), patch.object(main, "require_group_member", new=AsyncMock()), patch.object(main, "send_push_to_device", new=AsyncMock()) as send:
            await self.request_swap()
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM roster_change_requests WHERE status='open'"), 1)
        self.assertEqual(send.await_count, 2)
