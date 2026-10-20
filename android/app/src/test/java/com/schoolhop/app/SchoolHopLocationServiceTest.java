package com.schoolhop.app;

import static org.junit.Assert.*;
import android.Manifest;
import android.app.Application;
import android.app.Notification;
import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.location.Location;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Base64;
import com.google.android.gms.location.FusedLocationProviderClient;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.Shadows;
import org.robolectric.android.controller.ServiceController;
import org.robolectric.annotation.Config;
import org.robolectric.annotation.LooperMode;

/** Android 12 framework tests. Every HTTP connection and GPS subscription is fake. */
@RunWith(RobolectricTestRunner.class)
@Config(sdk = 31, manifest = Config.NONE)
@LooperMode(LooperMode.Mode.PAUSED)
public class SchoolHopLocationServiceTest {
    static final String USER = "00000000-0000-0000-0000-000000000001";
    static final String TRIP = "00000000-0000-0000-0000-000000000002";
    private ServiceController<TestService> controller;
    private TestService service;

    @Before public void setup() {
        Application app = RuntimeEnvironment.getApplication();
        Shadows.shadowOf(app).grantPermissions(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION);
        controller = Robolectric.buildService(TestService.class).create();
        service = controller.get();
        service.trackingPreferences().edit().clear().commit();
    }
    @After public void teardown() { controller.destroy(); }

    static String token() throws Exception {
        JSONObject claims = new JSONObject().put("sub", USER).put("exp", System.currentTimeMillis() / 1000 + 3600);
        return "test." + Base64.encodeToString(claims.toString().getBytes(StandardCharsets.UTF_8), Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING) + ".test";
    }
    static Intent startIntent(TestService service) throws Exception {
        return new Intent(service, TestService.class).setAction(SchoolHopLocationService.ACTION_START)
            .putExtra("tripId", TRIP).putExtra("token", token()).putExtra("apiBaseUrl", "https://api.example.test");
    }
    private void start() throws Exception { service.onStartCommand(startIntent(service), 0, 1); Shadows.shadowOf(Looper.getMainLooper()).idle(); }
    private void fix() {
        Location fix = new Location("fake");
        fix.setLatitude(1); fix.setLongitude(2); fix.setAccuracy(5);
        fix.setTime(System.currentTimeMillis()); fix.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos());
        service.acceptFix(fix); Shadows.shadowOf(Looper.getMainLooper()).idle();
    }

    @Test public void foregroundNotificationPrecedesValidationAndGpsSubscription() throws Exception {
        service.defer = true;
        service.onStartCommand(startIntent(service), 0, 1);
        Notification notification = Shadows.shadowOf((Service) service).getLastForegroundNotification();
        assertNotNull(notification);
        assertEquals(SchoolHopLocationService.CHANNEL_ID, notification.getChannelId());
        assertTrue((notification.flags & Notification.FLAG_ONGOING_EVENT) != 0);
        assertEquals(1, notification.actions.length);
        assertFalse(service.collecting);
        service.jobs.remove(0).run(); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertTrue(service.collecting);
    }

    @Test public void screenOffAndDeviceIdleDoNotSwitchToWebViewOrStopNativeUploads() throws Exception {
        start();
        PowerManager power = service.getSystemService(PowerManager.class);
        Shadows.shadowOf(power).setIsInteractive(false);
        Shadows.shadowOf(power).setIsDeviceIdleMode(true);
        service.sendBroadcast(new Intent(Intent.ACTION_SCREEN_OFF));
        for (int i = 0; i < 8; i++) {
            Shadows.shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(10));
            fix();
        }
        assertTrue(service.collecting);
        assertTrue(service.requests.stream().filter(c -> c.getRequestMethod().equals("POST")).count() >= 8);
        if (SchoolHopLocationDiagnostics.isEnabled()) {
            assertFalse(SchoolHopLocationDiagnostics.snapshot(service).getBoolean("interactive"));
            assertTrue(SchoolHopLocationDiagnostics.snapshot(service).getBoolean("idle"));
        }
    }

    @Test public void validationAndUploadsUseSameAuthenticatedTripAndNoRedirects() throws Exception {
        start(); fix();
        assertEquals(2, service.requests.size());
        FakeConnection validation = service.requests.get(0), upload = service.requests.get(1);
        assertEquals("GET", validation.getRequestMethod());
        assertEquals("POST", upload.getRequestMethod());
        assertEquals("https://api.example.test/api/trips/" + TRIP + "/locations", upload.getURL().toString());
        assertTrue(upload.getRequestProperty("Authorization").startsWith("Bearer test."));
        assertEquals(validation.getRequestProperty("Authorization"), upload.getRequestProperty("Authorization"));
        assertFalse(upload.getInstanceFollowRedirects());
        assertEquals(8000, upload.getReadTimeout());
        JSONObject body = new JSONObject(upload.output.toString(StandardCharsets.UTF_8.name()));
        assertTrue(body.has("latitude") && body.has("recorded_at"));
    }

    @Test public void inactiveTripOrDifferentDriverNeverCollectsGps() throws Exception {
        service.serverDriver = "00000000-0000-0000-0000-000000000003";
        start();
        assertFalse(service.collecting);
        assertFalse(SchoolHopLocationService.running());
        assertFalse(service.trackingPreferences().getBoolean("tracking", false));
    }

    @Test public void remoteTripCompletionStopsWithoutWaitingForMovement() throws Exception {
        start();
        service.serverState = "completed";
        Shadows.shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(31));
        assertFalse(service.collecting);
        assertFalse(SchoolHopLocationService.running());
        assertFalse(service.trackingPreferences().getBoolean("tracking", false));
    }

    @Test public void authorizationRevocationStopsUploadsAndClearsRecoveryCredentials() throws Exception {
        start(); service.uploadStatus = 401; fix();
        assertFalse(service.collecting);
        assertFalse(service.trackingPreferences().contains("token"));
        assertFalse(SchoolHopLocationService.running());
    }

    @Test public void duplicateStartAndDuplicateFixDoNotAddSubscriptionsOrUploads() throws Exception {
        Intent intent = startIntent(service);
        service.onStartCommand(intent, 0, 1); Shadows.shadowOf(Looper.getMainLooper()).idle();
        service.onStartCommand(intent, 0, 2); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertEquals(1, service.collectionStarts);
        fix(); fix();
        assertEquals(1, service.requests.stream().filter(c -> c.getRequestMethod().equals("POST")).count());
    }

    @Test public void stopActionClearsStateAndPreventsStickyResurrection() throws Exception {
        start();
        service.onStartCommand(new Intent().setAction(SchoolHopLocationService.ACTION_STOP), 0, 2);
        assertFalse(service.collecting);
        assertFalse(service.trackingPreferences().contains("token"));
        assertEquals(Service.START_NOT_STICKY, service.onStartCommand(null, 0, 3));
        assertFalse(SchoolHopLocationService.running());
    }

    @Test public void recoveryWithoutBackgroundPermissionFailsClosed() throws Exception {
        service.trackingPreferences().edit().putBoolean("tracking", true).putString("tripId", TRIP)
            .putString("token", token()).putString("apiBaseUrl", "https://api.example.test").commit();
        assertEquals(Service.START_NOT_STICKY, service.onStartCommand(null, 0, 1));
        assertFalse(service.collecting);
        assertTrue(service.requests.isEmpty());
    }

    @Test public void permittedStickyRecoveryRevalidatesBeforeCollecting() throws Exception {
        Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.ACCESS_BACKGROUND_LOCATION);
        service.trackingPreferences().edit().putBoolean("tracking", true).putString("tripId", TRIP)
            .putString("token", token()).putString("apiBaseUrl", "https://api.example.test").commit();
        service.defer = true;
        assertEquals(Service.START_STICKY, service.onStartCommand(null, 0, 1));
        assertFalse(service.collecting);
        service.jobs.remove(0).run(); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertEquals("GET", service.requests.get(0).getRequestMethod());
        assertTrue(service.collecting);
    }

    @Test public void recoveryOfAnEndedTripClearsStateWithoutCollecting() throws Exception {
        Shadows.shadowOf(RuntimeEnvironment.getApplication()).grantPermissions(Manifest.permission.ACCESS_BACKGROUND_LOCATION);
        service.trackingPreferences().edit().putBoolean("tracking", true).putString("tripId", TRIP)
            .putString("token", token()).putString("apiBaseUrl", "https://api.example.test").commit();
        service.serverState = "completed";
        service.onStartCommand(null, 0, 1); Shadows.shadowOf(Looper.getMainLooper()).idle();
        assertFalse(service.collecting);
        assertFalse(service.trackingPreferences().contains("token"));
        assertFalse(SchoolHopLocationService.running());
    }

    @Test public void coarsePermissionWorksAndRevocationStopsAtNextPulse() throws Exception {
        Shadows.shadowOf(RuntimeEnvironment.getApplication()).denyPermissions(Manifest.permission.ACCESS_FINE_LOCATION);
        start(); assertTrue(service.collecting);
        Shadows.shadowOf(RuntimeEnvironment.getApplication()).denyPermissions(Manifest.permission.ACCESS_COARSE_LOCATION);
        Shadows.shadowOf(Looper.getMainLooper()).idleFor(Duration.ofSeconds(1));
        assertFalse(service.collecting);
    }

    @Test public void invalidOriginsAndExpiredTokensAreRejectedWithoutNetwork() throws Exception {
        for (String url : new String[] { "http://api.example.test", "https://user:pass@api.example.test", "https://api.example.test/?token=private", "https://api.example.test/#private" }) {
            assertFalse(SchoolHopLocationService.validBaseUrl(url));
        }
        try { new SchoolHopLocationService.Session(TRIP, "not-a-jwt", "https://api.example.test"); fail(); }
        catch (Exception expected) { /* Input rejected before any HTTP request. */ }
        JSONObject expiredClaims = new JSONObject().put("sub", USER).put("exp", System.currentTimeMillis() / 1000 - 60);
        String expired = "test." + Base64.encodeToString(expiredClaims.toString().getBytes(StandardCharsets.UTF_8), Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING) + ".test";
        try { new SchoolHopLocationService.Session(TRIP, expired, "https://api.example.test"); fail(); }
        catch (Exception expected) { /* Expired sessions cannot restart GPS collection. */ }
        assertTrue(service.requests.isEmpty());
    }

    @Test public void diagnosticsContainNoCredentialsIdentifiersAddressesOrCoordinates() throws Exception {
        start(); fix();
        String snapshot = SchoolHopLocationDiagnostics.snapshot(service).toString();
        for (String secret : new String[] { "Bearer", "token", TRIP, USER, "api.example.test", "PRIVATE_ADDRESS", "latitude", "longitude" }) assertFalse(snapshot.contains(secret));
    }

    @Test public void productionStyleOffsetDeadlineAllowsCollectionAndNativeUpload() throws Exception {
        service.serverDeadline = "2099-10-20T05:29:20.625772+00:00";
        start();
        assertTrue(service.collecting);
        fix();
        assertEquals(1, service.requests.stream().filter(c -> c.getRequestMethod().equals("POST")).count());
        if (SchoolHopLocationDiagnostics.isEnabled()) {
            assertEquals("NONE", SchoolHopLocationDiagnostics.snapshot(service).getString("validationFailure"));
        }
    }

    @Test public void malformedDeadlineIsLocalRejectionAfterHttp200AndNeverCollects() throws Exception {
        service.serverDeadline = "PRIVATE_INVALID_TIMESTAMP";
        start();
        assertFalse(service.collecting);
        assertFalse(SchoolHopLocationService.running());
        if (SchoolHopLocationDiagnostics.isEnabled()) {
            JSONObject snapshot = SchoolHopLocationDiagnostics.snapshot(service);
            assertEquals(200, snapshot.getInt("httpStatus"));
            assertEquals(200, snapshot.getInt("validationHttpStatus"));
            assertEquals(409, snapshot.getInt("status"));
            assertEquals("INVALID_SAFETY_DEADLINE", snapshot.getString("validationFailure"));
            assertFalse(snapshot.toString().contains("PRIVATE_INVALID_TIMESTAMP"));
        }
    }

    @Test public void actualBackendConflictRemainsHttp409WithoutLocalParsingFailure() throws Exception {
        service.validationStatus = 409;
        service.serverDeadline = "PRIVATE_BODY_MUST_NOT_BE_PARSED";
        start();
        assertFalse(service.collecting);
        if (SchoolHopLocationDiagnostics.isEnabled()) {
            JSONObject snapshot = SchoolHopLocationDiagnostics.snapshot(service);
            assertEquals(409, snapshot.getInt("httpStatus"));
            assertEquals(409, snapshot.getInt("validationHttpStatus"));
            assertEquals("NONE", snapshot.getString("validationFailure"));
        }
    }

    @Test public void expiredSafetyDeadlineRemainsTerminalDespiteValidOffsetFormat() throws Exception {
        service.serverDeadline = Instant.ofEpochMilli(System.currentTimeMillis() - 60000).toString().replace("Z", "+00:00");
        start();
        assertFalse(service.collecting);
        if (SchoolHopLocationDiagnostics.isEnabled()) {
            assertEquals("DEADLINE_EXPIRED", SchoolHopLocationDiagnostics.snapshot(service).getString("validationFailure"));
        }
    }

    @Test public void delayedTripStillCannotCollectWithAValidOffsetDeadline() throws Exception {
        service.serverState = "delayed";
        service.serverDeadline = "2099-10-20T05:29:20.625772+00:00";
        start();
        assertFalse(service.collecting);
    }

    @Test public void endedTripStillCannotCollectWithAValidOffsetDeadline() throws Exception {
        service.serverEndedAt = "2026-10-20T02:29:20.625772+00:00";
        start();
        assertFalse(service.collecting);
    }

    @Test public void malformedResponseHasSanitizedLocalFailureMetadata() throws Exception {
        service.responseOverride = "{PRIVATE_INVALID_RESPONSE";
        start();
        assertFalse(service.collecting);
        if (SchoolHopLocationDiagnostics.isEnabled()) {
            JSONObject snapshot = SchoolHopLocationDiagnostics.snapshot(service);
            assertEquals(200, snapshot.getInt("httpStatus"));
            assertEquals("MALFORMED_RESPONSE", snapshot.getString("validationFailure"));
            assertFalse(snapshot.toString().contains("PRIVATE_INVALID_RESPONSE"));
        }
    }

    public static class TestService extends SchoolHopLocationService {
        boolean collecting, defer;
        int collectionStarts, uploadStatus = 200, validationStatus = 200;
        String serverState = "started", serverDriver = USER;
        String serverDeadline, serverEndedAt, responseOverride;
        final List<FakeConnection> requests = new ArrayList<>();
        final List<Runnable> jobs = new ArrayList<>();
        @Override protected FusedLocationProviderClient createLocationClient() { return null; }
        @Override protected SharedPreferences trackingPreferences() { return getSharedPreferences("location-test", MODE_PRIVATE); }
        @Override protected void setCollection(boolean enabled) {
            collecting = enabled;
            if (enabled) { collectionStarts++; broadcast(ACTION_TRACKING_READY, 0); }
        }
        @Override protected void executeNetwork(Runnable job) { if (defer) jobs.add(job); else job.run(); }
        @Override protected HttpURLConnection openConnection(String url) throws Exception {
            String body = new JSONObject().put("status", serverState).put("driver_user_id", serverDriver)
                .put("ended_at", serverEndedAt == null ? JSONObject.NULL : serverEndedAt)
                .put("safety_timeout_at", serverDeadline == null ? Instant.ofEpochMilli(System.currentTimeMillis() + 3600000).toString() : serverDeadline)
                .put("private_address", "PRIVATE_ADDRESS").toString();
            FakeConnection connection = new FakeConnection(new URL(url), url.endsWith("/locations") ? uploadStatus : validationStatus,
                responseOverride == null ? body : responseOverride);
            requests.add(connection); return connection;
        }
    }

    static class FakeConnection extends HttpURLConnection {
        final ByteArrayOutputStream output = new ByteArrayOutputStream();
        final int status; final String response;
        FakeConnection(URL url, int status, String response) { super(url); this.status = status; this.response = response; }
        @Override public OutputStream getOutputStream() { return output; }
        @Override public InputStream getInputStream() { return new ByteArrayInputStream(response.getBytes(StandardCharsets.UTF_8)); }
        @Override public int getResponseCode() { return status; }
        @Override public void disconnect() { }
        @Override public boolean usingProxy() { return false; }
        @Override public void connect() { }
    }
}
