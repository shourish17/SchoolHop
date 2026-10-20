"""APNs transport, cleanup and privacy checks. All Apple HTTP traffic is mocked."""
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

import app.main as main
from notification_test_support import RegistrationDatabase


class ApnsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = uuid4()
        self.device = {"id": uuid4(), "user_id": self.owner, "provider": "apns", "platform": "ios",
                       "token": "PRIVATE_TOKEN_CANARY", "last_seen_at": datetime.now(UTC), "enabled": True}
        self.db = RegistrationDatabase(self.device)
        self.client = AsyncMock()
        self.client.__aenter__.return_value = self.client
        self.client.post.return_value = self.response(200)
        for patcher in [patch.object(main.httpx, "AsyncClient", return_value=self.client),
                        patch.object(main, "apns_private_key", return_value="PRIVATE_KEY_CANARY"),
                        patch.object(main.jwt, "encode", return_value="PRIVATE_AUTH_CANARY"),
                        patch.object(main.settings, "apns_key_id", "synthetic-key"),
                        patch.object(main.settings, "apns_team_id", "synthetic-team"),
                        patch.object(main.settings, "apns_bundle_id", "com.schoolhop.app")]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, status, reason=None):
        return httpx.Response(status, json={"reason": reason}, request=httpx.Request("POST", "https://api.push.apple.com/3/device/PRIVATE_TOKEN_CANARY"))

    async def deliver(self, kind="handover"):
        await main.deliver_push(self.db, self.owner, kind, "PRIVATE_CHILD_CANARY", "PRIVATE_NOTE_CANARY", "trip", uuid4())

    async def test_production_and_explicit_sandbox_use_correct_alert_transport(self):
        for sandbox, host in [(False, "api.push.apple.com"), (True, "api.sandbox.push.apple.com")]:
            with self.subTest(sandbox=sandbox), patch.object(main.settings, "apns_use_sandbox", sandbox):
                await self.deliver()
                args, kwargs = self.client.post.call_args
                self.assertEqual(httpx.URL(args[0]).host, host)
                self.assertEqual(kwargs["headers"]["apns-topic"], "com.schoolhop.app")
                self.assertEqual(kwargs["headers"]["apns-push-type"], "alert")
                self.assertEqual(kwargs["headers"]["apns-priority"], "10")
                self.assertIn("alert", kwargs["json"]["aps"])
                self.assertEqual(kwargs["json"]["aps"]["sound"], "default")
                self.assertNotIn("PRIVATE_CHILD", str(kwargs["json"]))
                self.assertNotIn("PRIVATE_NOTE", str(kwargs["json"]))
                main.httpx.AsyncClient.assert_called_with(http2=True, timeout=8)

    async def test_all_trip_events_include_deep_links_and_no_child_information(self):
        for kind in ["driver_assignment", "trip_swap_request", "trip_swap_decision", "trip_start", "delay", "handover"]:
            with self.subTest(kind=kind):
                await self.deliver(kind)
                data = self.client.post.call_args.kwargs["json"]
                self.assertEqual(data["kind"], kind)
                self.assertTrue(data["action_url"].startswith("/?tab=trip&trip="))

    async def test_bad_environment_topic_credentials_and_transient_failures_preserve_token(self):
        for status, reason in [(400, "BadDeviceToken"), (400, "DeviceTokenNotForTopic"), (400, "BadTopic"),
                               (403, "InvalidProviderToken"), (429, "TooManyRequests"), (500, "InternalServerError")]:
            with self.subTest(reason=reason):
                self.client.post.return_value = self.response(status, reason)
                with self.assertLogs("schoolhop", level="WARNING") as captured:
                    await self.deliver()
                self.assertEqual(self.db.cleaned, 0)
                self.assertNotIn("PRIVATE_", "\n".join(captured.output))
                self.assertIn(reason, "\n".join(captured.output))

    async def test_unregistered_token_is_disabled_without_leaking_provider_url(self):
        self.client.post.return_value = self.response(410, "Unregistered")
        with self.assertLogs("schoolhop", level="WARNING") as captured:
            await self.deliver()
        self.assertEqual(self.db.cleaned, 1)
        self.assertNotIn("PRIVATE_", "\n".join(captured.output))

    async def test_registration_refreshed_after_send_is_not_disabled(self):
        async def post(*args, **kwargs):
            # Emulate refreshed registration after APNs handled the old token.
            self.db.records[self.device["id"]]["last_seen_at"] = datetime.now(UTC)
            return self.response(410, "Unregistered")
        self.client.post.side_effect = post
        await self.deliver()
        self.assertEqual(self.db.cleaned, 0)

    async def test_logout_or_reassignment_before_send_prevents_delivery(self):
        snapshot = dict(self.device)
        for change in [{"enabled": False}, {"user_id": uuid4()}]:
            with self.subTest(change=change):
                self.db.records[self.device["id"]].update(change)
                with patch.object(self.db, "fetch", new=AsyncMock(return_value=[snapshot])):
                    await self.deliver()
                self.client.post.assert_not_awaited()

    async def test_missing_configuration_is_visible_and_does_not_delete_device(self):
        with patch.object(main.settings, "apns_key_id", None), self.assertLogs("schoolhop", level="WARNING") as captured:
            await self.deliver()
        self.assertIn("not configured", "\n".join(captured.output))
        self.client.post.assert_not_awaited()
        self.assertEqual(self.db.cleaned, 0)
