import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

import app.main as main


class FirebasePushTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_v1_uses_private_preview_and_android_channel(self):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = httpx.Response(200, request=httpx.Request("POST", "https://fcm.googleapis.com"))
        with patch.object(main.settings, "fcm_service_account_path", "/external/credentials.json"), patch.object(
            main, "fcm_access_token", new=AsyncMock(return_value="oauth-token")
        ), patch.object(main.httpx, "AsyncClient", return_value=client):
            await main.send_push_to_device({"provider": "fcm", "token": "device-token"}, "Child Name picked up", "Private address", {"action_url": "/?tab=trip&trip=trip-id"})
        args, kwargs = client.post.call_args
        self.assertEqual(args[0], "https://fcm.googleapis.com/v1/projects/schoolhop-1c00d/messages:send")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer oauth-token")
        message = kwargs["json"]["message"]
        self.assertEqual(message["token"], "device-token")
        self.assertEqual(message["android"]["notification"]["channel_id"], "schoolhop_activity")
        self.assertEqual(message["android"]["notification"]["visibility"], "PRIVATE")
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
        ), patch.object(main.Path, "read_text", return_value=json.dumps({"client_email": "sender@example.com", "private_key": "test-key"})), patch.object(
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
                database = SimpleNamespace(fetch=AsyncMock(return_value=[{"id": uuid4(), "provider": "fcm"}]), execute=AsyncMock())
                details = [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}] if code else []
                response = httpx.Response(status, json={"error": {"details": details}}, request=httpx.Request("POST", "https://fcm.googleapis.com"))
                error = httpx.HTTPStatusError("provider error", request=response.request, response=response)
                with patch.object(main, "send_push_to_device", new=AsyncMock(side_effect=error)):
                    await main.deliver_push(database, uuid4(), "handover", "title", "body", "trip", uuid4())
                self.assertEqual(database.execute.await_count, int(disable))

    async def test_all_activity_types_reach_devices_with_action_routes(self):
        kinds = ["driver_assignment", "roster", "roster_response", "trip_swap_request", "trip_swap_decision", "delay", "handover", "invitation"]
        for kind in kinds:
            with self.subTest(kind=kind):
                entity_type = "invitation" if kind == "invitation" else "trip"
                entity_id = uuid4()
                device = {"provider": "fcm", "token": "device-token"}
                database = SimpleNamespace(fetch=AsyncMock(return_value=[device]), fetchrow=AsyncMock(return_value={"id": uuid4()}))
                with patch.object(main, "send_push_to_device", new=AsyncMock()) as send:
                    await main.notify(database, uuid4(), kind, "title", "body", entity_type, entity_id, action_url="/?invite=test-code" if kind == "invitation" else None)
                send.assert_awaited_once()
                data = send.call_args.args[3]
                self.assertEqual(data["kind"], kind)
                self.assertTrue(data["action_url"].startswith("/?"))
                if entity_type == "trip":
                    self.assertIn(str(entity_id), data["action_url"])
