package com.schoolhop.app;

import android.Manifest;
import android.os.Build;
import androidx.core.app.NotificationManagerCompat;
import com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin;
import com.getcapacitor.JSObject;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.google.firebase.messaging.FirebaseMessaging;
import com.google.firebase.messaging.RemoteMessage;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;

@CapacitorPlugin(
    name = "PushNotifications",
    permissions = @Permission(strings = { Manifest.permission.POST_NOTIFICATIONS }, alias = "receive")
)
public class SchoolHopPushNotificationsPlugin extends PushNotificationsPlugin {
    @Override
    public void load() {
        super.load();
        // The stock plugin handles warm intents only. Retain a cold-start tap for JS.
        handleOnNewIntent(getActivity().getIntent());
        SchoolHopPushDiagnostics.log(getContext(), "plugin_loaded");
    }

    @Override
    protected void handleOnResume() {
        super.handleOnResume();
        SchoolHopPushDiagnostics.log(getContext(), "app_resumed");
    }

    @Override
    @PluginMethod
    public void checkPermissions(PluginCall call) {
        SchoolHopPushDiagnostics.log(getContext(), "permission_checked");
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            JSObject result = new JSObject();
            result.put("receive", NotificationManagerCompat.from(getContext()).areNotificationsEnabled() ? "granted" : "denied");
            call.resolve(result);
        } else {
            super.checkPermissions(call);
        }
    }

    @Override
    @PluginMethod
    public void requestPermissions(PluginCall call) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) checkPermissions(call);
        else super.requestPermissions(call);
    }

    @Override
    @PluginMethod
    public void createChannel(PluginCall call) {
        super.createChannel(call);
        SchoolHopPushDiagnostics.log(getContext(), "channel_created");
    }

    @Override
    public void fireNotification(RemoteMessage message) {
        SchoolHopPushDiagnostics.log(getContext(), "capacitor_notification_handling_started");
        try {
            super.fireNotification(message);
            SchoolHopPushDiagnostics.log(getContext(), "capacitor_notification_handling_completed");
        } catch (RuntimeException exception) {
            // Do not log exception messages, RemoteMessage, or intent extras.
            SchoolHopPushDiagnostics.log(getContext(), "capacitor_notification_handling_failed");
            throw exception;
        }
    }

    @Override
    @PluginMethod
    public void register(PluginCall call) {
        SchoolHopPushDiagnostics.log(getContext(), "registration_requested");
        if (!SchoolHopFirebaseConfig.hasFirebaseMessagingConfig(getContext())) {
            sendError("Firebase Messaging is not configured for this Android build");
            call.resolve();
            return;
        }
        try {
            super.register(call);
        } catch (IllegalStateException exception) {
            sendError("Firebase Messaging registration failed: " + exception.getMessage());
            call.resolve();
        }
    }

    @Override
    @PluginMethod
    public void unregister(PluginCall call) {
        if (!SchoolHopFirebaseConfig.hasFirebaseMessagingConfig(getContext())) {
            call.resolve();
            return;
        }
        try {
            FirebaseMessaging messaging = FirebaseMessaging.getInstance();
            messaging.setAutoInitEnabled(false);
            messaging.deleteToken().addOnCompleteListener(task -> {
                if (task.isSuccessful()) call.resolve();
                else call.reject("Firebase Messaging unregister failed");
            });
        } catch (IllegalStateException exception) {
            sendError("Firebase Messaging unregister failed: " + exception.getMessage());
            call.resolve();
        }
    }
}
