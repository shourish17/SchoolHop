package com.schoolhop.app;

import static org.junit.Assert.*;
import android.Manifest;
import android.app.Application;
import android.content.Context;
import android.content.Intent;
import android.os.Looper;
import androidx.appcompat.app.AppCompatActivity;
import com.getcapacitor.JSObject;
import com.getcapacitor.PluginCall;
import java.time.Duration;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 31, manifest = Config.NONE)
@LooperMode(LooperMode.Mode.PAUSED)
public class SchoolHopLocationPluginTest {
    private ActivityController<AppCompatActivity> activity;
    private TestPlugin plugin;
    private Application app;

    @Before public void setup() {
        app = RuntimeEnvironment.getApplication();
        activity = Robolectric.buildActivity(AppCompatActivity.class);
        activity.get().setTheme(androidx.appcompat.R.style.Theme_AppCompat);
        activity.setup();
        plugin = new TestPlugin(); plugin.activity = activity.get();
    }
    @After public void teardown() { activity.pause().stop().destroy(); }

    Capture call(String method) throws Exception {
        JSObject input = new JSObject().put("tripId", SchoolHopLocationServiceTest.TRIP)
            .put("token", SchoolHopLocationServiceTest.token()).put("apiBaseUrl", "https://api.example.test");
        return new Capture(method, input);
    }

    @Test public void backgroundedActivityCannotStartForegroundService() throws Exception {
        activity.pause().stop();
        Capture call = call("startTripTracking");
        plugin.startTripTracking(call); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertNotNull(call.error);
        assertNull(Shadows.shadowOf(app).getNextStartedService());
        activity.start().resume();
    }

    @Test public void trackingMethodsAreExportedToTheCapacitorBridge() throws Exception {
        for (String method : new String[] { "startTripTracking", "stopTripTracking", "requestLocationUpdate", "status", "getTrackingDiagnostics" }) {
            assertTrue(SchoolHopLocationPlugin.class.getDeclaredMethod(method, PluginCall.class)
                .isAnnotationPresent(com.getcapacitor.PluginMethod.class));
        }
    }

    @Test public void androidBridgePayloadLoggingIsDisabledEvenInDebugBuilds() throws Exception {
        app.getApplicationInfo().flags |= android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE;
        try (java.io.InputStream input = app.getAssets().open("capacitor.config.json")) {
            String config = new String(input.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
            assertEquals("none", new org.json.JSONObject(config).getJSONObject("android").getString("loggingBehavior"));
        }
        com.getcapacitor.CapConfig config = com.getcapacitor.CapConfig.loadDefault(app);
        assertFalse(config.isLoggingEnabled());
        com.getcapacitor.Logger.init(config);
        com.getcapacitor.Logger.verbose("PRIVATE_TOKEN_CANARY");
        for (org.robolectric.shadows.ShadowLog.LogItem entry : org.robolectric.shadows.ShadowLog.getLogsForTag("Capacitor")) {
            assertFalse(entry.msg.contains("PRIVATE_TOKEN_CANARY"));
        }
    }

    @Test public void preciseOrApproximatePermissionIsAcceptedWithoutAllTimePrompt() throws Exception {
        Shadows.shadowOf(app).denyPermissions(Manifest.permission.ACCESS_FINE_LOCATION);
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION);
        Capture call = call("startTripTracking");
        plugin.startTripTracking(call); Shadows.shadowOf(Looper.getMainLooper()).idle();
        Intent start = Shadows.shadowOf(app).getNextStartedService();
        assertNotNull(start);
        assertEquals(SchoolHopLocationService.ACTION_START, start.getAction());
        assertNull(plugin.requestedPermission);
        assertNull(call.result); // Native readiness, not merely startForegroundService(), resolves startup.
        plugin.stopTripTracking(call("stopTripTracking")); Shadows.shadowOf(Looper.getMainLooper()).idle();
    }

    @Test public void denialRejectsWithoutStartingAnyService() throws Exception {
        Shadows.shadowOf(app).denyPermissions(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION);
        Capture call = call("startTripTracking");
        plugin.startTripTracking(call); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertEquals("foregroundLocation", plugin.requestedPermission);
        java.lang.reflect.Method callback = SchoolHopLocationPlugin.class.getDeclaredMethod("foregroundPermissionCallback", PluginCall.class);
        callback.setAccessible(true); callback.invoke(plugin, call);
        Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertNotNull(call.error);
        assertNull(Shadows.shadowOf(app).getNextStartedService());
    }

    @Test public void startupTimeoutDoesNotLeaveTrackingIntentBehind() throws Exception {
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.ACCESS_COARSE_LOCATION);
        Capture call = call("startTripTracking");
        plugin.startTripTracking(call); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertNotNull(Shadows.shadowOf(app).getNextStartedService());
        Shadows.shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(31));
        assertNotNull(call.error);
        assertNull(Shadows.shadowOf(app).getNextStartedService());
    }

    @Test public void stoppingAnInactiveServiceNeverColdStartsForegroundWork() throws Exception {
        Capture call = call("stopTripTracking");
        plugin.stopTripTracking(call); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertNotNull(call.result);
        assertFalse(call.result.getBoolean("tracking"));
        assertNull(Shadows.shadowOf(app).getNextStartedService());
    }

    static final class Capture extends PluginCall {
        JSObject result; String error;
        Capture(String method, JSObject data) { super(null, "SchoolHopLocation", "test", method, data); }
        @Override public void resolve(JSObject data) { result = data; }
        @Override public void reject(String message) { error = message; }
    }
    static final class TestPlugin extends SchoolHopLocationPlugin {
        AppCompatActivity activity; String requestedPermission;
        @Override public Context getContext() { return RuntimeEnvironment.getApplication(); }
        @Override public AppCompatActivity getActivity() { return activity; }
        @Override protected void requestPermissionForAlias(String alias, PluginCall call, String callbackName) { requestedPermission = alias; }
        @Override protected void notifyListeners(String event, JSObject data, boolean retain) { }
    }
}
