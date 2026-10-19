package com.schoolhop.app;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;

public class SchoolHopFirebaseConfigTest {
    @Test
    public void guardedPushMethodsAreExportedToCapacitor() throws Exception {
        for (String method : new String[] { "register", "unregister", "checkPermissions", "requestPermissions", "createChannel" }) {
            assertTrue(SchoolHopPushNotificationsPlugin.class.getDeclaredMethod(method, PluginCall.class).isAnnotationPresent(PluginMethod.class));
        }
        assertTrue(SchoolHopConfigPlugin.class.getDeclaredMethod("getNotificationDiagnostics", PluginCall.class).isAnnotationPresent(PluginMethod.class));
    }

    @Test
    public void missingFirebaseValuesDisableMessaging() {
        assertFalse(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("", ""));
        assertFalse(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("1:android:abc", ""));
        assertFalse(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("", "123456789"));
    }

    @Test
    public void configuredFirebaseValuesEnableMessaging() {
        assertTrue(SchoolHopFirebaseConfig.hasRequiredFirebaseValues("1:123456789:android:abcdef", "123456789"));
    }
}
