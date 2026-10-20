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


@unittest.skipUnless(SOCKET, "requires disposable PostgreSQL Unix socket")
class PostgresHandoverConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Actual PostgreSQL row locks and unique notification index; no real pushes."""
    response = PostgresNotificationConcurrencyTests.response
    asyncTearDown = PostgresNotificationConcurrencyTests.asyncTearDown

    async def asyncSetUp(self):
        await PostgresNotificationConcurrencyTests.asyncSetUp(self)
        await self.database.execute("""
            ALTER TABLE trips ADD driver_user_id uuid, ADD group_id uuid, ADD trip_type text;
            CREATE TABLE children (id uuid PRIMARY KEY, parent_id uuid, name text);
            ALTER TABLE trip_children ADD pickup_status text DEFAULT 'pending', ADD dropoff_status text DEFAULT 'pending';
            ALTER TABLE trip_children ADD PRIMARY KEY (trip_id, child_id);
            CREATE TABLE handovers (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), trip_id uuid, child_id uuid,
                handover_type text, completed_by uuid, note text, completed_at timestamptz DEFAULT now(),
                UNIQUE(trip_id, child_id, handover_type));
        """)
        self.child_id, self.parent_id = uuid4(), uuid4()
        await self.database.execute("UPDATE trips SET driver_user_id=$2, group_id=$3, trip_type='pickup' WHERE id=$1",
                                    self.trip_id, self.owner, self.group_id)
        await self.database.execute("INSERT INTO children VALUES ($1,$2,'Synthetic child')", self.child_id, self.parent_id)
        await self.database.execute("INSERT INTO trip_children (trip_id,child_id) VALUES ($1,$2)", self.trip_id, self.child_id)
        self.trip = await self.database.fetchrow("SELECT * FROM trips WHERE id=$1", self.trip_id)
        self.push = AsyncMock()
        for patcher in [patch.object(main, "require_trip_driver", new=AsyncMock(return_value=self.trip)),
                        patch.object(main, "deliver_push", new=self.push)]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def handover(self, action="picked_up", note="Original"):
        return await main.handover(self.trip_id, self.child_id, main.HandoverIn(handover_type=action, note=note),
                                   {"id": str(self.owner)}, self.database)

    async def test_concurrent_duplicates_create_one_handover_notification_and_push(self):
        async with asyncio.timeout(5):
            results = await asyncio.gather(*(self.handover(note=f"Synthetic note {i}") for i in range(8)))
        self.assertEqual(len({row["id"] for row in results}), 1)
        self.assertEqual(len({row["note"] for row in results}), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM handovers"), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM notifications"), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM audit_records"), 1)
        self.push.assert_awaited_once()
        self.assertEqual(await self.database.fetchval("SELECT pickup_status FROM trip_children"), "picked_up")

    async def test_concurrent_absence_and_handover_cannot_overwrite_each_other(self):
        async with asyncio.timeout(5):
            results = await asyncio.gather(self.handover(), self.handover("absent"), return_exceptions=True)
        self.assertEqual(sum(isinstance(row, HTTPException) and row.status_code == 409 for row in results), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM handovers"), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM notifications"), 1)
        self.push.assert_awaited_once()

    async def test_legacy_record_is_preserved_and_does_not_permit_wrong_trip_action(self):
        legacy_id = uuid4()
        await self.database.execute("INSERT INTO handovers (id,trip_id,child_id,handover_type,completed_by,note) VALUES ($1,$2,$3,'dropped_off',$4,'Historical')",
                                    legacy_id, self.trip_id, self.child_id, self.owner)
        with self.assertRaises(HTTPException) as error:
            await self.handover("dropped_off")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(await self.database.fetchval("SELECT note FROM handovers WHERE id=$1", legacy_id), "Historical")
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM notifications"), 0)

    async def test_current_trip_type_and_driver_are_checked_under_lock(self):
        await self.database.execute("UPDATE trips SET trip_type='dropoff' WHERE id=$1", self.trip_id)
        with self.assertRaises(HTTPException):
            await self.handover("picked_up")
        await self.handover("dropped_off")
        self.assertEqual(await self.database.fetchval("SELECT dropoff_status FROM trip_children"), "dropped_off")
        await self.database.execute("UPDATE trips SET driver_user_id=$2 WHERE id=$1", self.trip_id, self.replacement)
        with self.assertRaises(HTTPException) as error:
            await self.handover("dropped_off")
        self.assertEqual(error.exception.status_code, 403)


    async def test_legacy_absence_remains_terminal_under_concurrent_old_client_requests(self):
        await self.database.execute("UPDATE trips SET trip_type='dropoff' WHERE id=$1", self.trip_id)
        await self.database.execute("UPDATE trip_children SET pickup_status='absent', dropoff_status='pending'")
        await self.database.execute("INSERT INTO handovers (trip_id,child_id,handover_type,completed_by,note) VALUES ($1,$2,'absent',$3,'Legacy absence')", self.trip_id, self.child_id, self.owner)
        async with asyncio.timeout(5):
            results = await asyncio.gather(self.handover("absent"), self.handover("dropped_off"), return_exceptions=True)
        self.assertIsInstance(results[1], HTTPException)
        self.assertEqual(results[1].status_code, 409)
        self.assertEqual(results[0]["note"], "Legacy absence")
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM handovers"), 1)
        self.assertEqual(await self.database.fetchval("SELECT dropoff_status FROM trip_children"), "pending")
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM audit_records"), 0)

    async def test_audit_failure_rolls_back_handover_and_status(self):
        await self.database.execute("ALTER TABLE audit_records ADD CONSTRAINT reject_test_audit CHECK (action <> 'trip.handover')")
        with self.assertRaises(asyncpg.CheckViolationError):
            await self.handover()
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM handovers"), 0)
        self.assertEqual(await self.database.fetchval("SELECT pickup_status FROM trip_children"), "pending")
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM notifications"), 0)
        self.push.assert_not_awaited()
        await self.database.execute("ALTER TABLE audit_records DROP CONSTRAINT reject_test_audit")
        await self.handover()
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM audit_records"), 1)

    async def test_notification_failure_leaves_atomic_audit_and_retry_does_not_duplicate(self):
        with patch.object(main, "notify", new=AsyncMock(side_effect=RuntimeError("synthetic failure"))):
            with self.assertRaises(RuntimeError):
                await self.handover()
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM handovers"), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM audit_records"), 1)
        await self.handover()
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM audit_records"), 1)
        self.assertEqual(await self.database.fetchval("SELECT count(*) FROM notifications"), 1)

    async def test_legacy_absence_read_query_preserves_stored_history(self):
        await self.database.execute("UPDATE trips SET trip_type='dropoff' WHERE id=$1", self.trip_id)
        await self.database.execute("UPDATE trip_children SET pickup_status='absent', dropoff_status='pending'")
        await self.database.execute("INSERT INTO handovers (trip_id,child_id,handover_type,completed_by,note) VALUES ($1,$2,'absent',$3,'Historical')", self.trip_id, self.child_id, self.owner)
        await self.database.execute("""
            CREATE TABLE carpool_groups (id uuid PRIMARY KEY, created_by uuid);
            ALTER TABLE children ADD year_group text, ADD pickup_notes text, ADD emergency_contact_name text,
                ADD emergency_contact_phone text, ADD home_address text, ADD home_city text, ADD home_postcode text,
                ADD home_country text, ADD home_latitude double precision, ADD home_longitude double precision,
                ADD home_place_id text;
        """)
        await self.database.execute("INSERT INTO carpool_groups VALUES ($1,$2)", self.group_id, self.organiser)
        trip = await self.database.fetchrow("SELECT * FROM trips WHERE id=$1", self.trip_id)
        for user_id in [self.owner, self.parent_id]:
            children = await main.visible_trip_children(self.database, user_id, trip)
            self.assertEqual(children[0]["dropoff_status"], "absent")
            self.assertNotIn("handover_absent", children[0])
        self.assertEqual(await self.database.fetchval("SELECT dropoff_status FROM trip_children"), "pending")


@unittest.skipUnless(SOCKET, "requires disposable PostgreSQL Unix socket")
class PostgresApnsRegistrationTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = PostgresNotificationConcurrencyTests.asyncTearDown
    task = PostgresNotificationConcurrencyTests.task
    wait_for_blocked_queries = PostgresNotificationConcurrencyTests.wait_for_blocked_queries

    def response(self, status):
        return httpx.Response(status, json={"reason": "Unregistered"} if status == 410 else {},
                              request=httpx.Request("POST", "https://api.push.apple.com/3/device/synthetic"))

    async def asyncSetUp(self):
        await PostgresNotificationConcurrencyTests.asyncSetUp(self)
        await self.database.execute("UPDATE mobile_devices SET provider='apns',platform='ios' WHERE id=$1", self.device_id)
        for patcher in [patch.object(main.settings, "apns_key_id", "synthetic-key"),
                        patch.object(main.settings, "apns_team_id", "synthetic-team"),
                        patch.object(main.settings, "apns_bundle_id", "com.schoolhop.app"),
                        patch.object(main, "apns_private_key", return_value="synthetic-key"),
                        patch.object(main.jwt, "encode", return_value="synthetic-assertion")]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def deliver(self):
        await main.deliver_push(self.database, self.owner, "handover", "Private child", "Private note", "trip", self.trip_id)

    async def test_logout_and_reassignment_wait_for_inflight_apns_attempt(self):
        for kind in ["logout", "reassignment"]:
            with self.subTest(kind=kind):
                await self.database.execute("UPDATE mobile_devices SET user_id=$2,enabled=true,last_seen_at=now() WHERE id=$1", self.device_id, self.owner)
                entered, release = asyncio.Event(), asyncio.Event()
                self.client.post.reset_mock()
                async def post(*args, **kwargs):
                    entered.set()
                    await release.wait()
                    return self.response(200)
                self.client.post.side_effect = post
                delivery = self.task(self.deliver())
                await asyncio.wait_for(entered.wait(), 5)
                payload = main.MobileDeviceIn(provider="apns", platform="ios", token="synthetic-test-device")
                writer = self.task(main.unregister_mobile_device(payload, {"id": str(self.owner)}, self.database) if kind == "logout"
                                   else main.register_mobile_device(payload, {"id": str(self.replacement)}, self.database))
                await self.wait_for_blocked_queries("UPDATE mobile_devices" if kind == "logout" else "INSERT INTO mobile_devices", 1)
                self.assertFalse(writer.done())
                release.set()
                await asyncio.wait_for(asyncio.gather(delivery, writer), 5)
                self.client.post.assert_awaited_once()

    async def test_registration_mutation_winning_lock_cancels_apns_attempt(self):
        for kind in ["logout", "reassignment", "refresh"]:
            with self.subTest(kind=kind):
                await self.database.execute("UPDATE mobile_devices SET user_id=$2,enabled=true,last_seen_at=now() WHERE id=$1", self.device_id, self.owner)
                async with self.database.acquire() as writer:
                    async with writer.transaction():
                        await writer.execute("UPDATE mobile_devices SET user_id=$2,enabled=$3,last_seen_at=now() WHERE id=$1", self.device_id,
                                             self.replacement if kind == "reassignment" else self.owner, kind != "logout")
                        delivery = self.task(self.deliver())
                        await self.wait_for_blocked_queries("SELECT id FROM mobile_devices", 1)
                await asyncio.wait_for(delivery, 5)
                self.client.post.assert_not_awaited()
