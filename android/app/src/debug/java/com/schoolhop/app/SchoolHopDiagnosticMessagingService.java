package com.schoolhop.app;

import android.content.Intent;
import android.os.Bundle;
import com.capacitorjs.plugins.pushnotifications.MessagingService;
import com.google.firebase.messaging.NotificationParams;
import com.google.firebase.messaging.RemoteMessage;

/** Temporary debug wrapper. Delegates to the SDK exactly once and never logs message contents. */
public class SchoolHopDiagnosticMessagingService extends MessagingService {
    private boolean callbackInvoked;

    @Override
    public void onCreate() {
        super.onCreate();
        SchoolHopPushDiagnostics.log(this, "messaging_service_created");
    }

    @Override
    public void handleIntent(Intent intent) {
        callbackInvoked = false;
        Bundle extras = intent.getExtras();
        boolean notificationPayload = extras != null && NotificationParams.isNotification(extras);
        String action = intent.getAction();
        String kind = "com.google.android.c2dm.intent.RECEIVE".equals(action)
            || "com.google.firebase.messaging.RECEIVE_DIRECT_BOOT".equals(action) ? "message"
            : "com.google.firebase.messaging.NEW_TOKEN".equals(action) ? "token" : "other";
        SchoolHopPushDiagnostics.dispatchStarted(this, kind, notificationPayload);
        try {
            // Background notification payloads can be consumed here without onMessageReceived.
            super.handleIntent(intent);
            SchoolHopPushDiagnostics.dispatchCompleted(this, notificationPayload, callbackInvoked);
        } catch (RuntimeException exception) {
            SchoolHopPushDiagnostics.log(this, "sdk_intent_dispatch_failed");
            throw exception;
        }
    }

    @Override
    public void onMessageReceived(RemoteMessage message) {
        callbackInvoked = true;
        SchoolHopPushDiagnostics.messageReceived(this, message);
        super.onMessageReceived(message);
    }

    @Override
    public void onNewToken(String token) {
        SchoolHopPushDiagnostics.log(this, "token_callback_observed");
        super.onNewToken(token);
    }
}
