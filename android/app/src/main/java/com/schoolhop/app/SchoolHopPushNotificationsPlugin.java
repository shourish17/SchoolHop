package com.schoolhop.app;

import android.Manifest;
import com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.google.firebase.messaging.FirebaseMessaging;
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
    }

    @Override
    @PluginMethod
    public void register(PluginCall call) {
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
