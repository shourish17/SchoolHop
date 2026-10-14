package com.schoolhop.app;

import android.Manifest;
import com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;

@CapacitorPlugin(
    name = "PushNotifications",
    permissions = @Permission(strings = { Manifest.permission.POST_NOTIFICATIONS }, alias = "receive")
)
public class SchoolHopPushNotificationsPlugin extends PushNotificationsPlugin {
    @Override
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
    public void unregister(PluginCall call) {
        if (!SchoolHopFirebaseConfig.hasFirebaseMessagingConfig(getContext())) {
            call.resolve();
            return;
        }
        try {
            super.unregister(call);
        } catch (IllegalStateException exception) {
            sendError("Firebase Messaging unregister failed: " + exception.getMessage());
            call.resolve();
        }
    }
}
