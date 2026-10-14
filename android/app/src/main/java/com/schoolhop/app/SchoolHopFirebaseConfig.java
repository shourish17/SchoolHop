package com.schoolhop.app;

import android.content.Context;
import android.content.res.Resources;

final class SchoolHopFirebaseConfig {
    private SchoolHopFirebaseConfig() {}

    static boolean hasFirebaseMessagingConfig(Context context) {
        return hasRequiredFirebaseValues(
            readStringResource(context, "google_app_id"),
            readStringResource(context, "gcm_defaultSenderId")
        );
    }

    static boolean hasRequiredFirebaseValues(String googleAppId, String senderId) {
        return isNonBlank(googleAppId) && isNonBlank(senderId);
    }

    private static boolean isNonBlank(String value) {
        return value != null && !value.trim().isEmpty();
    }

    private static String readStringResource(Context context, String name) {
        int id = context.getResources().getIdentifier(name, "string", context.getPackageName());
        if (id == 0) return "";
        try {
            return context.getString(id);
        } catch (Resources.NotFoundException exception) {
            return "";
        }
    }
}
