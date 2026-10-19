package com.schoolhop.app;

import android.annotation.SuppressLint;
import android.app.ActivityManager;
import android.app.KeyguardManager;
import android.app.NotificationChannel;
import android.app.NotificationChannelGroup;
import android.app.NotificationManager;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Process;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;
import androidx.core.app.NotificationManagerCompat;
import com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.google.firebase.messaging.RemoteMessage;
import java.util.Arrays;
import java.util.List;

/** Temporary, process-local diagnostics. Only explicit safe fields may enter snapshots/logs. */
final class SchoolHopPushDiagnostics {
    static final String TAG = "SchoolHopPushDebug";
    private static final String CHANNEL_ID = "schoolhop_activity";
    private static int receivedCallbacks;
    private static int receivedMessageIntents;
    private static int dispatchCompletions;
    private static long lastReceiptElapsedMs = -1;
    private static boolean lastHasNotification;
    private static boolean lastHasData;
    private static String lastDispatch = "none";
    private static String lastIntentKind = "none";
    private static boolean lastIntentHasNotification;

    private SchoolHopPushDiagnostics() {}
    static boolean isEnabled() { return true; }

    static void log(Context context, String event) {
        try {
            JSObject result = snapshot(context);
            result.put("event", event);
            Log.i(TAG, result.toString());
        } catch (RuntimeException exception) {
            // Never include exception messages, intent extras, payloads, or tokens.
            Log.i(TAG, "diagnostic_snapshot_unavailable");
        }
    }

    static synchronized void messageReceived(Context context, RemoteMessage message) {
        receivedCallbacks++;
        lastReceiptElapsedMs = SystemClock.elapsedRealtime();
        lastHasNotification = message.getNotification() != null;
        lastHasData = !message.getData().isEmpty();
        log(context, "onMessageReceived");
    }

    static synchronized void dispatchStarted(Context context, String kind, boolean notificationPayload) {
        lastIntentKind = kind;
        lastIntentHasNotification = notificationPayload;
        if ("message".equals(kind)) receivedMessageIntents++;
        log(context, "sdk_intent_dispatch_started");
    }

    static synchronized void dispatchCompleted(Context context, boolean notificationPayload, boolean callbackInvoked) {
        dispatchCompletions++;
        lastDispatch = callbackInvoked ? "forwarded_to_capacitor"
            : notificationPayload ? "sdk_consumed_notification_without_callback"
            : "completed_without_message_callback";
        log(context, "sdk_intent_dispatch_completed");
    }

    static synchronized JSObject snapshot(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        boolean appEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled();
        ActivityManager.RunningAppProcessInfo process = new ActivityManager.RunningAppProcessInfo();
        ActivityManager.getMyMemoryState(process);
        JSObject result = new JSObject();
        result.put("debugOnly", true);
        result.put("packageName", context.getPackageName());
        result.put("uid", Process.myUid());
        result.put("androidUserId", Process.myUid() / 100000);
        result.put("apiLevel", Build.VERSION.SDK_INT);
        result.put("targetSdk", context.getApplicationInfo().targetSdkVersion);
        result.put("processImportance", process.importance);
        result.put("processForeground", process.importance == ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND);
        result.put("deviceLocked", context.getSystemService(KeyguardManager.class).isKeyguardLocked());
        result.put("notificationsEnabledCompat", appEnabled);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            result.put("notificationsEnabledFramework", manager.areNotificationsEnabled());
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            result.put("notificationsPaused", manager.areNotificationsPaused());
        }
        result.put("firebaseConfigured", SchoolHopFirebaseConfig.hasFirebaseMessagingConfig(context));
        result.put("activeNotificationCount", manager.getActiveNotifications().length);
        result.put("channel", channelStatus(context, manager, appEnabled));
        addServiceStatus(context, result);
        PushNotificationsPlugin plugin = PushNotificationsPlugin.getPushNotificationsInstance();
        result.put("capacitorPushPluginLoaded", plugin != null);
        if (plugin != null) {
            String[] presentation = plugin.getConfig().getArray("presentationOptions");
            result.put("foregroundAlertConfigured", presentation != null && Arrays.asList(presentation).contains("alert"));
        }
        result.put("receivedCallbackCount", receivedCallbacks);
        result.put("receivedMessageIntentCount", receivedMessageIntents);
        result.put("dispatchCompletionCount", dispatchCompletions);
        result.put("lastReceiptElapsedMs", lastReceiptElapsedMs);
        result.put("lastCallbackPayloadHasNotification", lastHasNotification);
        result.put("lastCallbackPayloadHasData", lastHasData);
        result.put("lastIntentKind", lastIntentKind);
        result.put("lastIntentHasNotificationPayload", lastIntentHasNotification);
        result.put("lastDispatch", lastDispatch);
        return result;
    }

    private static JSObject channelStatus(Context context, NotificationManager manager, boolean appEnabled) {
        JSObject result = new JSObject();
        result.put("id", CHANNEL_ID);
        result.put("supported", Build.VERSION.SDK_INT >= Build.VERSION_CODES.O);
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return result;
        NotificationChannel channel = manager.getNotificationChannel(CHANNEL_ID);
        result.put("exists", channel != null);
        result.put("importance", channel == null ? -1 : channel.getImportance());
        boolean groupBlocked = false;
        if (channel != null && channel.getGroup() != null && Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            NotificationChannelGroup group = manager.getNotificationChannelGroup(channel.getGroup());
            groupBlocked = group != null && group.isBlocked();
        }
        result.put("groupBlocked", groupBlocked);
        result.put("enabled", appEnabled && channel != null && channel.getImportance() != NotificationManager.IMPORTANCE_NONE && !groupBlocked);
        if (channel != null) {
            result.put("lockscreenVisibility", channel.getLockscreenVisibility());
            result.put("sound", soundStatus(context, channel.getSound()));
        }
        return result;
    }

    // Resource reflection is intentional: inspect an existing, possibly invalid channel URI.
    @SuppressLint("DiscouragedApi")
    static JSObject soundStatus(Context context, Uri uri) {
        JSObject result = new JSObject();
        String kind = uri == null ? "silent" : Settings.System.DEFAULT_NOTIFICATION_URI.equals(uri)
            ? "system_default" : ContentResolver.SCHEME_ANDROID_RESOURCE.equals(uri.getScheme())
            ? "android_resource" : "other";
        result.put("kind", kind);
        if ("android_resource".equals(kind)) {
            List<String> parts = uri.getPathSegments();
            boolean exists = false;
            if (context.getPackageName().equals(uri.getAuthority()) && parts.size() == 2) {
                exists = context.getResources().getIdentifier(parts.get(1), parts.get(0), context.getPackageName()) != 0;
            }
            result.put("resourceExists", exists);
        }
        // Do not include arbitrary sound URIs, which may refer to personal media.
        return result;
    }

    @SuppressWarnings("deprecation")
    // This query is restricted to our own package, which is always visible to itself.
    @SuppressLint("QueryPermissionsNeeded")
    private static void addServiceStatus(Context context, JSObject result) {
        PackageManager packages = context.getPackageManager();
        Intent event = new Intent("com.google.firebase.MESSAGING_EVENT").setPackage(context.getPackageName());
        JSArray candidates = new JSArray();
        for (ResolveInfo info : packages.queryIntentServices(event, 0)) {
            JSObject service = new JSObject();
            service.put("className", info.serviceInfo.name);
            service.put("enabled", info.serviceInfo.enabled);
            service.put("priority", info.priority);
            candidates.put(service);
        }
        result.put("messagingServices", candidates);
        ResolveInfo selected = packages.resolveService(event, 0);
        result.put("resolvedMessagingService", selected == null ? "none" : selected.serviceInfo.name);
        result.put("diagnosticServiceSelected", selected != null && SchoolHopDiagnosticMessagingService.class.getName().equals(selected.serviceInfo.name));
        try {
            Bundle metadata = packages.getApplicationInfo(context.getPackageName(), PackageManager.GET_META_DATA).metaData;
            result.put("defaultChannelMatches", metadata != null && CHANNEL_ID.equals(metadata.getString("com.google.firebase.messaging.default_notification_channel_id")));
        } catch (PackageManager.NameNotFoundException exception) {
            result.put("defaultChannelMatches", false);
        }
    }
}
