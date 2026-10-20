import asyncio
import json
import unittest
import xml.etree.ElementTree as ET
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

import app.main as main
from notification_test_support import RegistrationDatabase


class FirebasePushTests(unittest.IsolatedAsyncioTestCase):
    def test_android_manifest_default_matches_sender_channel(self):
        manifest = ET.parse(Path(__file__).resolve().parents[1] / "android/app/src/main/AndroidManifest.xml")
        android = "{http://schemas.android.com/apk/res/android}"
        channel = next(node for node in manifest.findall("application/meta-data") if node.get(android + "name") == "com.google.firebase.messaging.default_notification_channel_id")
        self.assertEqual(channel.get(android + "value"), "schoolhop_activity")

    async def test_http_v1_uses_private_preview_and_android_channel(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://fcm.googleapis.com"))
        with patch.object(main.settings, "fcm_service_account_path", "/external/credentials.json"), patch.object(
            main, "fcm_access_token", new=AsyncMock(return_value="oauth-token")
        ), patch.object(main.httpx, "AsyncClient", return_value=client):
            await main.send_push_to_device({"provider": "fcm", "token": "device-token"}, "Child Name picked up", "Private address", {"action_url": "/?tab=trip&trip=trip-id"}, registration_guard=lambda: nullcontext(True))
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "https://fcm.googleapis.com/v1/projects/schoolhop-1c00d/messages:send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer oauth-token")
        message = kwargs["json"]["message"]
        self.assertEqual(message["token"], "device-token")
        self.assertEqual(message["android"]["notification"]["channel_id"], "schoolhop_activity")
        self.assertEqual(message["android"]["notification"]["visibility"], "PRIVATE")
        self.assertEqual(message["android"]["priority"], "high")
        self.assertEqual(message["android"]["ttl"], "60s")
        self.assertNotIn("Child Name", json.dumps(message))
        self.assertNotIn("Private address", json.dumps(message))

    async def test_no_credentials_skips_fcm_without_network(self):
        with patch.object(main.settings, "fcm_service_account_path", None), patch.object(main.httpx, "AsyncClient") as client:
            await main.send_push_to_device({"provider": "fcm"}, "title", "body", {})
        client.assert_not_called()

    async def test_oauth_exchange_caches_and_renews_short_lived_token(self):
        client = AsyncMock()
        client.post.return_value = httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600}, request=httpx.Request("POST", "https://oauth2.googleapis.com/token"))
        with patch.object(main.settings, "fcm_service_account_path", "/external/credentials.json"), patch.object(
            main, "_fcm_access_token", None
        ), patch.object(main.Path, "read_text", return_value=json.dumps({"type": "service_account", "project_id": "schoolhop-1c00d", "token_uri": "https://oauth2.googleapis.com/token", "client_email": "sender@example.com", "private_key": "test-key"})), patch.object(
            main.jwt, "encode", return_value="signed-assertion"
        ) as encode, patch.object(main.time_module, "time", return_value=1000) as now:
            self.assertEqual(await main.fcm_access_token(client), "access-token")
            self.assertEqual(await main.fcm_access_token(client), "access-token")
            client.post.assert_awaited_once()
            claims = encode.call_args.args[0]
            self.assertEqual(claims["scope"], "https://www.googleapis.com/auth/firebase.messaging")
            self.assertEqual(encode.call_args.kwargs["algorithm"], "RS256")
            now.return_value = 4600
            await main.fcm_access_token(client)
            self.assertEqual(client.post.await_count, 2)

    async def test_unregister_is_scoped_to_authenticated_owner_provider_and_token(self):
        database = SimpleNamespace(execute=AsyncMock())
        user_id = uuid4()
        result = await main.unregister_mobile_device(main.MobileDeviceIn(provider="fcm", token="device-token"), {"id": str(user_id)}, database)
        query, *args = database.execute.call_args.args
        self.assertIn("WHERE user_id = $1 AND provider = $2 AND token = $3", query)
        self.assertEqual(args, [user_id, "fcm", "device-token"])
        self.assertEqual(result, {"status": "unregistered"})

    async def test_registration_reassigns_token_and_does_not_return_or_audit_token(self):
        database = SimpleNamespace(fetchrow=AsyncMock(return_value={"enabled": True}), execute=AsyncMock())
        user_id = uuid4()
        with patch.object(main, "audit", new=AsyncMock()) as audit:
            result = await main.register_mobile_device(main.MobileDeviceIn(provider="fcm", token="private-device-token", platform="android"), {"id": str(user_id)}, database)
        self.assertIn("user_id = EXCLUDED.user_id", database.fetchrow.call_args.args[0])
        self.assertNotIn("private-device-token", repr(audit.call_args))
        self.assertNotIn("token", result)

    async def test_only_unregistered_fcm_tokens_are_disabled(self):
        for status, code, disable in [(404, "UNREGISTERED", True), (403, "SENDER_ID_MISMATCH", False), (400, "INVALID_ARGUMENT", False), (401, None, False), (503, None, False)]:
            with self.subTest(status=status, code=code):
                database = SimpleNamespace(fetch=AsyncMock(return_value=[{"id": uuid4(), "provider": "fcm", "token": "test-device", "last_seen_at": datetime.now(UTC)}]), execute=AsyncMock())
                details = [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}] if code else []
                response = httpx.Response(status, json={"error": {"details": details}}, request=httpx.Request("POST", "https://fcm.googleapis.com"))
                error = httpx.HTTPStatusError("provider error", request=response.request, response=response)
                with patch.object(main, "send_push_to_device", new=AsyncMock(side_effect=error)):
                    await main.deliver_push(database, uuid4(), "handover", "title", "body", "trip", uuid4())
                self.assertEqual(database.execute.await_count, int(disable))

    async def test_all_activity_types_reach_devices_with_action_routes(self):
        kinds = ["driver_assignment", "roster", "roster_response", "trip_swap_request", "trip_swap_decision", "trip_start", "delay", "handover", "invitation"]
        for kind in kinds:
            with self.subTest(kind=kind):
                entity_type = "invitation" if kind == "invitation" else "trip"
                entity_id = uuid4()
                device = {"id": uuid4(), "provider": "fcm", "token": "device-token", "last_seen_at": datetime.now(UTC)}
                database = SimpleNamespace(fetch=AsyncMock(return_value=[device]), fetchrow=AsyncMock(return_value={"id": uuid4()}))
                with patch.object(main, "send_push_to_device", new=AsyncMock()) as send:
                    await main.notify(database, uuid4(), kind, "title", "body", entity_type, entity_id, action_url="/?invite=test-code" if kind == "invitation" else None)
                send.assert_awaited_once()
                data = send.call_args.args[3]
                self.assertEqual(data["kind"], kind)
                self.assertTrue(data["action_url"].startswith("/?"))
                if entity_type == "trip":
                    self.assertIn(str(entity_id), data["action_url"])


def provider_response(status, *, details=None, headers=None, host="fcm.googleapis.com"):
    return httpx.Response(status, json={"error": {"details": details or []}}, headers=headers,
                          request=httpx.Request("POST", f"https://{host}/send"))


FCM_ERROR_TYPE = "type.googleapis.com/google.firebase.fcm.v1.FcmError"
BAD_REQUEST_TYPE = "type.googleapis.com/google.rpc.BadRequest"


class FirebaseReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # No real HTTP client, credentials, database, or sleeps in these tests.
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.client.post.return_value = provider_response(200)
        self.device = {"id": uuid4(), "provider": "fcm", "token": "PRIVATE_DEVICE_CANARY", "last_seen_at": datetime.now(UTC)}
        self.user_id = uuid4()
        guard_database = RegistrationDatabase({**self.device, "user_id": self.user_id, "enabled": True})
        self.database = SimpleNamespace(fetch=AsyncMock(return_value=[self.device]), execute=AsyncMock(),
                                        fetchrow=AsyncMock(return_value={"id": uuid4()}), acquire=guard_database.acquire)
        for patcher in [
            patch.object(main.settings, "fcm_service_account_path", "/fake/service-account.json"),
            patch.object(main, "_fcm_access_token", None),
            patch.object(main, "fcm_access_token", new=AsyncMock(return_value="PRIVATE_ACCESS_CANARY")),
            patch.object(main.httpx, "AsyncClient", return_value=self.client),
            patch.object(main.asyncio, "sleep", new=AsyncMock()),
            patch.object(main.secrets, "randbelow", return_value=0),
        ]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def send(self):
        await main.send_push_to_device(self.device, "PRIVATE_CHILD_CANARY", "PRIVATE_ADDRESS_CANARY", {}, registration_guard=lambda: nullcontext(True))

    async def notify(self):
        await main.notify(self.database, self.user_id, "handover", "Private child picked up", "Private address", "trip", uuid4())

    async def test_transient_failures_retry_with_exponential_backoff_and_stop_on_success(self):
        self.client.post.side_effect = [provider_response(503), provider_response(500), provider_response(200)]
        await self.send()
        self.assertEqual(self.client.post.await_count, 3)
        self.assertEqual([call.args[0] for call in main.asyncio.sleep.await_args_list], [10, 20])
        bodies = [call.kwargs["json"] for call in self.client.post.await_args_list]
        self.assertEqual(bodies, [bodies[0]] * 3)

    async def test_retry_jitter_is_added_without_shortening_backoff(self):
        self.client.post.side_effect = [provider_response(503), provider_response(200)]
        with patch.object(main.secrets, "randbelow", return_value=500):
            await self.send()
        main.asyncio.sleep.assert_awaited_once_with(10.5)

    async def test_retry_after_seconds_is_honored(self):
        self.client.post.side_effect = [provider_response(503, headers={"Retry-After": "15"}), provider_response(200)]
        await self.send()
        main.asyncio.sleep.assert_awaited_once_with(15)

    async def test_retry_after_http_date_is_honored(self):
        self.client.post.side_effect = [provider_response(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}), provider_response(200)]
        with patch.object(main.time_module, "time", return_value=1445412465):
            await self.send()
        main.asyncio.sleep.assert_awaited_once_with(15)

    async def test_invalid_retry_after_uses_default_backoff(self):
        self.client.post.side_effect = [provider_response(503, headers={"Retry-After": "invalid"}), provider_response(200)]
        await self.send()
        main.asyncio.sleep.assert_awaited_once_with(10)

    async def test_quota_and_long_retry_after_are_not_retried_early(self):
        for status, headers in [(429, {}), (429, {"Retry-After": "1"}), (503, {"Retry-After": "120"})]:
            with self.subTest(status=status, headers=headers):
                self.client.post.reset_mock()
                self.client.post.return_value = provider_response(status, headers=headers)
                with self.assertRaises(httpx.HTTPStatusError):
                    await self.send()
                self.client.post.assert_awaited_once()
        main.asyncio.sleep.assert_not_awaited()

    async def test_connect_failure_is_retried(self):
        self.client.post.side_effect = [httpx.ConnectError("PRIVATE_ERROR_CANARY"), provider_response(200)]
        await self.send()
        self.assertEqual(self.client.post.await_count, 2)
        main.asyncio.sleep.assert_awaited_once_with(10)

    async def test_ambiguous_transport_failures_are_not_resent(self):
        for error in [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError, httpx.CloseError]:
            with self.subTest(error=error.__name__):
                self.client.post.reset_mock()
                self.client.post.side_effect = error("PRIVATE_ERROR_CANARY")
                await self.notify()
                self.client.post.assert_awaited_once()
        main.asyncio.sleep.assert_not_awaited()
        self.database.execute.assert_not_awaited()

    async def test_connection_and_pool_timeouts_are_safe_to_retry(self):
        for error in [httpx.ConnectTimeout, httpx.PoolTimeout]:
            with self.subTest(error=error.__name__):
                self.client.post.reset_mock()
                main.asyncio.sleep.reset_mock()
                self.client.post.side_effect = [error("PRIVATE_ERROR_CANARY"), provider_response(200)]
                await self.send()
                self.assertEqual(self.client.post.await_count, 2)
                main.asyncio.sleep.assert_awaited_once_with(10)

    async def test_attempts_are_bounded_and_inbox_survives_exhaustion(self):
        self.client.post.return_value = provider_response(503)
        await self.notify()
        self.assertEqual(self.client.post.await_count, 3)
        self.database.fetchrow.assert_awaited_once()
        self.assertIn("INSERT INTO notifications", self.database.fetchrow.call_args.args[0])
        self.database.execute.assert_not_awaited()

    async def test_auth_payload_and_project_errors_do_not_retry_or_disable_devices(self):
        for status in [400, 401, 403, 404]:
            with self.subTest(status=status):
                self.client.post.reset_mock()
                self.client.post.return_value = provider_response(status)
                await self.notify()
                self.client.post.assert_awaited_once()
        main.asyncio.sleep.assert_not_awaited()
        self.database.execute.assert_not_awaited()

    async def test_rejected_cached_access_token_is_evicted(self):
        self.client.post.return_value = provider_response(401)
        with patch.object(main, "_fcm_access_token", ("/fake/service-account.json", "PRIVATE_ACCESS_CANARY", 9999999999)):
            await self.notify()
            self.assertIsNone(main._fcm_access_token)

    async def test_401_does_not_evict_a_concurrently_refreshed_access_token(self):
        self.client.post.return_value = provider_response(401)
        cache = ("/fake/service-account.json", "newer-access-token", 9999999999)
        with patch.object(main, "_fcm_access_token", cache):
            await self.notify()
            self.assertEqual(main._fcm_access_token, cache)

    async def test_cleanup_is_scoped_to_exact_registration_snapshot(self):
        self.client.post.return_value = provider_response(404, details=[{"@type": FCM_ERROR_TYPE, "errorCode": "UNREGISTERED"}])
        await self.notify()
        sql, *args = self.database.execute.call_args.args
        self.assertEqual(args, [self.device["id"], self.user_id, "fcm", self.device["token"], self.device["last_seen_at"]])
        for condition in ["id = $1", "user_id = $2", "provider = $3", "token = $4", "last_seen_at = $5"]:
            self.assertIn(condition, sql)

    async def test_explicit_invalid_token_field_is_cleaned_but_generic_invalid_argument_is_not(self):
        self.client.post.return_value = provider_response(400, details=[{"@type": BAD_REQUEST_TYPE, "fieldViolations": [{"field": "message.token"}]}])
        await self.notify()
        self.database.execute.assert_awaited_once()
        self.database.execute.reset_mock()
        for details in [
            [{"@type": FCM_ERROR_TYPE, "errorCode": "INVALID_ARGUMENT"}],
            [{"@type": BAD_REQUEST_TYPE, "fieldViolations": [{"field": "message.data"}]}],
            [{"@type": BAD_REQUEST_TYPE, "fieldViolations": [{"field": "message.token"}, {"field": "message.android.ttl"}]}],
        ]:
            self.client.post.return_value = provider_response(400, details=details)
            await self.notify()
        self.database.execute.assert_not_awaited()

    async def test_malformed_error_bodies_never_cleanup_or_break_alerts(self):
        bodies = [None, [], {"error": None}, {"error": {"details": "bad"}}, {"error": {"details": [None]}}]
        for body in bodies:
            self.client.post.return_value = httpx.Response(404, json=body, request=httpx.Request("POST", "https://fcm.googleapis.com/send"))
            await self.notify()
        self.client.post.return_value = httpx.Response(404, content=b"invalid JSON", request=httpx.Request("POST", "https://fcm.googleapis.com/send"))
        await self.notify()
        self.database.execute.assert_not_awaited()
        self.assertEqual(self.database.fetchrow.await_count, len(bodies) + 1)

    async def test_oauth_errors_cannot_invalidate_registration(self):
        error_response = provider_response(404, host="oauth2.googleapis.com", details=[{"@type": FCM_ERROR_TYPE, "errorCode": "UNREGISTERED"}])
        main.fcm_access_token.side_effect = httpx.HTTPStatusError("PRIVATE_OAUTH_CANARY", request=error_response.request, response=error_response)
        await self.notify()
        self.database.execute.assert_not_awaited()
        self.client.post.assert_not_awaited()

    async def test_cleanup_failure_does_not_block_other_devices_or_inbox(self):
        other = {**self.device, "id": uuid4(), "provider": "apns", "token": "ios-device"}
        self.database.fetch.return_value = [self.device, other]
        self.database.execute.side_effect = main.asyncpg.PostgresError("PRIVATE_DB_CANARY")
        error_response = provider_response(404, details=[{"@type": FCM_ERROR_TYPE, "errorCode": "UNREGISTERED"}])
        with patch.object(main, "send_push_to_device", new=AsyncMock(side_effect=[httpx.HTTPStatusError("provider error", request=error_response.request, response=error_response), None])) as send:
            await self.notify()
        self.assertEqual(send.await_count, 2)
        self.assertEqual(send.await_args_list[1].args[0]["provider"], "apns")
        self.database.fetchrow.assert_awaited_once()

    async def test_lookup_failure_keeps_in_app_alert(self):
        self.database.fetch.side_effect = main.asyncpg.PostgresError("PRIVATE_DB_CANARY")
        await self.notify()
        self.database.fetchrow.assert_awaited_once()
        self.client.post.assert_not_awaited()

    async def test_delivery_timeout_is_contained(self):
        main.fcm_access_token.side_effect = TimeoutError()
        await self.notify()
        self.database.fetchrow.assert_awaited_once()
        self.database.execute.assert_not_awaited()

    async def test_total_deadline_cancels_a_stalled_transport(self):
        async def stall(*args, **kwargs):
            await asyncio.Event().wait()
        self.client.post.side_effect = stall
        with patch.object(main, "FCM_DELIVERY_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                await self.send()
        self.client.post.assert_awaited_once()

    async def test_cancellation_is_propagated_without_retry(self):
        self.client.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.send()
        self.client.post.assert_awaited_once()
        main.asyncio.sleep.assert_not_awaited()

    async def test_logs_never_include_tokens_payloads_or_provider_error_bodies(self):
        self.client.post.return_value = httpx.Response(503, json={"error": {"message": "PRIVATE_PROVIDER_CANARY"}}, request=httpx.Request("POST", "https://fcm.googleapis.com"))
        with self.assertLogs(level="WARNING") as captured:
            await self.notify()
        self.assertNotIn("PRIVATE", "\n".join(captured.output))

    async def test_apns_transport_auth_and_action_route_are_preserved(self):
        with patch.object(main.settings, "apns_key_id", "test-key-id"), patch.object(main.settings, "apns_team_id", "test-team"), patch.object(main.settings, "apns_bundle_id", "com.schoolhop.mobile"), patch.object(main.settings, "apns_use_sandbox", False), patch.object(main, "apns_private_key", return_value="fake-private-key"), patch.object(main.jwt, "encode", return_value="fake-apns-assertion") as encode:
            await main.send_push_to_device({"provider": "apns", "token": "ios-device"}, "Private title", "Private body", {"action_url": "/?tab=alerts"}, registration_guard=lambda: nullcontext(True))
        args, kwargs = self.client.post.call_args
        self.assertEqual(args[0], "https://api.push.apple.com/3/device/ios-device")
        self.assertEqual(kwargs["headers"]["apns-topic"], "com.schoolhop.mobile")
        self.assertEqual(kwargs["headers"]["apns-push-type"], "alert")
        self.assertEqual(kwargs["json"]["action_url"], "/?tab=alerts")
        self.assertEqual(kwargs["json"]["aps"]["sound"], "default")
        self.assertEqual(encode.call_args.kwargs["algorithm"], "ES256")
        main.fcm_access_token.assert_not_awaited()

    async def test_permanent_apns_error_still_cleans_up_that_registration(self):
        self.device["provider"] = "apns"
        response = httpx.Response(410, json={"reason": "Unregistered"}, request=httpx.Request("POST", "https://api.push.apple.com/send"))
        with patch.object(main, "send_push_to_device", new=AsyncMock(side_effect=httpx.HTTPStatusError("gone", request=response.request, response=response))):
            await self.notify()
        self.database.execute.assert_awaited_once()
        self.assertEqual(self.database.execute.call_args.args[1], self.device["id"])

    async def test_accepted_duplicate_inbox_event_does_not_send_twice(self):
        self.database.fetchrow.side_effect = [{"id": uuid4()}, None]
        for _ in range(2):
            await main.notify(self.database, self.user_id, "trip_start", "Trip started", "Body", idempotency_key="same-trip-start")
        self.client.post.assert_awaited_once()


class FirebaseCredentialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AsyncMock()
        self.client.post.return_value = httpx.Response(200, json={"access_token": "fake-access-token", "expires_in": 3600}, request=httpx.Request("POST", "https://oauth2.googleapis.com/token"))
        self.credentials = {"type": "service_account", "project_id": "schoolhop-1c00d", "token_uri": "https://oauth2.googleapis.com/token", "client_email": "test@example.com", "private_key": "fake-key"}
        for patcher in [patch.object(main.settings, "fcm_service_account_path", "/fake/service-account.json"), patch.object(main, "_fcm_access_token", None), patch.object(main, "_fcm_token_lock", asyncio.Lock()), patch.object(main.jwt, "encode", return_value="fake-assertion")]:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_concurrent_sends_share_one_oauth_exchange(self):
        with patch.object(main.Path, "read_text", return_value=json.dumps(self.credentials)):
            tokens = await asyncio.gather(*(main.fcm_access_token(self.client) for _ in range(10)))
        self.assertEqual(tokens, ["fake-access-token"] * 10)
        self.client.post.assert_awaited_once()

    async def test_oauth_exchange_retries_transient_failure_without_logging_assertion(self):
        self.client.post.side_effect = [provider_response(503, host="oauth2.googleapis.com"), self.client.post.return_value]
        with patch.object(main.Path, "read_text", return_value=json.dumps(self.credentials)), patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep, patch.object(main.secrets, "randbelow", return_value=0):
            result = await main.fcm_access_token(self.client)
        self.assertEqual(result, "fake-access-token")
        self.assertEqual(self.client.post.await_count, 2)
        sleep.assert_awaited_once_with(10)

    async def test_changing_credential_path_requires_new_exchange(self):
        with patch.object(main.Path, "read_text", return_value=json.dumps(self.credentials)):
            await main.fcm_access_token(self.client)
            with patch.object(main.settings, "fcm_service_account_path", "/fake/other-service-account.json"):
                await main.fcm_access_token(self.client)
        self.assertEqual(self.client.post.await_count, 2)

    async def test_wrong_project_or_credential_type_fails_before_network(self):
        for changes in [{"project_id": "other-project"}, {"type": "authorized_user"}, {"token_uri": "https://untrusted.invalid/token"}]:
            with patch.object(main.Path, "read_text", return_value=json.dumps({**self.credentials, **changes})):
                with self.assertRaises(ValueError):
                    await main.fcm_access_token(self.client)
        self.client.post.assert_not_awaited()

    async def test_missing_credential_file_does_not_use_network(self):
        with patch.object(main.Path, "read_text", side_effect=FileNotFoundError()):
            with self.assertRaises(FileNotFoundError):
                await main.fcm_access_token(self.client)
        self.client.post.assert_not_awaited()

    async def test_invalid_oauth_response_is_not_cached(self):
        for response in [{"access_token": "", "expires_in": 3600}, {"access_token": "fake", "expires_in": 0}]:
            self.client.post.return_value = httpx.Response(200, json=response, request=httpx.Request("POST", "https://oauth2.googleapis.com/token"))
            with patch.object(main.Path, "read_text", return_value=json.dumps(self.credentials)):
                with self.assertRaises(ValueError):
                    await main.fcm_access_token(self.client)
            self.assertIsNone(main._fcm_access_token)
