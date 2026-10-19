package com.schoolhop.app;

import static org.junit.Assert.*;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import androidx.core.app.NotificationManagerCompat;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import com.getcapacitor.JSObject;
import com.getcapacitor.PluginCall;
import com.google.firebase.messaging.RemoteMessage;
import org.junit.Assume;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Read-only diagnostics tests: no FCM send, local notification post, or settings changes. */
@RunWith(AndroidJUnit4.class)
public class NotificationDiagnosticsTest {
    private Context context() {
        return InstrumentationRegistry.getInstrumentation().getTargetContext();
    }

    @Test
    public void snapshotMatchesActualFrameworkAndChannelState() throws Exception {
        Context context = context();
        JSObject status = SchoolHopPushDiagnostics.snapshot(context);
        assertTrue(status.getBoolean("debugOnly"));
        assertEquals(Build.VERSION.SDK_INT, status.getInt("apiLevel"));
        assertEquals(NotificationManagerCompat.from(context).areNotificationsEnabled(), status.getBoolean("notificationsEnabledCompat"));
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = context.getSystemService(NotificationManager.class).getNotificationChannel("schoolhop_activity");
            assertEquals(channel != null, status.getJSONObject("channel").getBoolean("exists"));
            assertEquals(channel == null ? -1 : channel.getImportance(), status.getJSONObject("channel").getInt("importance"));
        }
    }

    @Test
    public void diagnosticServiceReplacesCapacitorWithoutCompetingAtSamePriority() throws Exception {
        Context context = context();
        Intent event = new Intent("com.google.firebase.MESSAGING_EVENT").setPackage(context.getPackageName());
        ResolveInfo selected = context.getPackageManager().resolveService(event, 0);
        assertNotNull(selected);
        assertEquals(SchoolHopDiagnosticMessagingService.class.getName(), selected.serviceInfo.name);
        assertFalse(selected.serviceInfo.exported);
        int selectedPriorityCount = 0;
        for (ResolveInfo candidate : context.getPackageManager().queryIntentServices(event, 0)) {
            assertNotEquals("com.capacitorjs.plugins.pushnotifications.MessagingService", candidate.serviceInfo.name);
            if (candidate.priority == selected.priority) selectedPriorityCount++;
        }
        assertEquals(1, selectedPriorityCount);
        assertTrue(SchoolHopPushDiagnostics.snapshot(context).getBoolean("diagnosticServiceSelected"));
    }

    @Test
    public void payloadContentsAndPrivateSoundUrisNeverEnterSnapshots() throws Exception {
        Bundle payload = new Bundle();
        payload.putString("gcm.n.e", "1");
        payload.putString("gcm.n.title", "PRIVATE_TITLE_CANARY");
        payload.putString("gcm.n.body", "PRIVATE_BODY_CANARY");
        payload.putString("google.message_id", "PRIVATE_ID_CANARY");
        payload.putString("private_data", "PRIVATE_DATA_CANARY");
        // Record shape only; never delegate this synthetic message to a notification service.
        SchoolHopPushDiagnostics.messageReceived(context(), new RemoteMessage(payload));
        JSObject status = SchoolHopPushDiagnostics.snapshot(context());
        assertTrue(status.getBoolean("lastCallbackPayloadHasNotification"));
        assertTrue(status.getBoolean("lastCallbackPayloadHasData"));
        assertFalse(status.toString().contains("CANARY"));
        assertFalse(SchoolHopPushDiagnostics.soundStatus(context(), Uri.parse("content://private/PRIVATE_URI_CANARY")).toString().contains("CANARY"));
        assertFalse(SchoolHopPushDiagnostics.soundStatus(context(), Uri.parse("android.resource://com.schoolhop.app/raw/default")).getBoolean("resourceExists"));
    }

    @Test
    public void backgroundSdkDispatchDoesNotRequireAnOnMessageReceivedCallback() throws Exception {
        SchoolHopPushDiagnostics.dispatchStarted(context(), "message", true);
        SchoolHopPushDiagnostics.dispatchCompleted(context(), true, false);
        JSObject background = SchoolHopPushDiagnostics.snapshot(context());
        assertEquals("message", background.getString("lastIntentKind"));
        assertTrue(background.getBoolean("lastIntentHasNotificationPayload"));
        assertEquals("sdk_consumed_notification_without_callback", background.getString("lastDispatch"));
        SchoolHopPushDiagnostics.dispatchCompleted(context(), true, true);
        assertEquals("forwarded_to_capacitor", SchoolHopPushDiagnostics.snapshot(context()).getString("lastDispatch"));
    }

    @Test
    public void android12PermissionCheckUsesActualAppPermission() throws Exception {
        Assume.assumeTrue(Build.VERSION.SDK_INT < 33);
        Context context = context();
        SchoolHopPushNotificationsPlugin plugin = new SchoolHopPushNotificationsPlugin() {
            @Override public Context getContext() { return context; }
        };
        class Capture extends PluginCall {
            JSObject result;
            Capture() { super(null, "PushNotifications", "test", "checkPermissions", new JSObject()); }
            @Override public void resolve(JSObject data) { result = data; }
        }
        Capture checked = new Capture();
        plugin.checkPermissions(checked);
        assertEquals(NotificationManagerCompat.from(context).areNotificationsEnabled() ? "granted" : "denied", checked.result.getString("receive"));
        Capture requested = new Capture();
        plugin.requestPermissions(requested);
        assertEquals(checked.result.getString("receive"), requested.result.getString("receive"));
    }
}
