package com.schoolhop.app;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.os.SystemClock;
import android.util.Base64;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;
import androidx.core.content.ContextCompat;
import com.google.android.gms.location.CurrentLocationRequest;
import com.google.android.gms.location.FusedLocationProviderClient;
import com.google.android.gms.location.LocationCallback;
import com.google.android.gms.location.LocationRequest;
import com.google.android.gms.location.LocationResult;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.Priority;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.DateTimeException;
import java.util.Locale;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.json.JSONObject;

/** A visible, trip-scoped native service; the WebView is not involved in uploads. */
public class SchoolHopLocationService extends Service {
    static final String ACTION_START = "com.schoolhop.app.location.START";
    static final String ACTION_STOP = "com.schoolhop.app.location.STOP";
    static final String ACTION_TRACKING_READY = "com.schoolhop.app.location.TRACKING_READY";
    static final String ACTION_TRACKING_STOPPED = "com.schoolhop.app.location.TRACKING_STOPPED";
    static final String ACTION_TRACKING_ERROR = "com.schoolhop.app.location.TRACKING_ERROR";
    static final String CHANNEL_ID = "schoolhop_active_trip_location";
    static final int NOTIFICATION_ID = 17017;
    static final long LOCATION_INTERVAL_MS = 10000;

    private static volatile SchoolHopLocationService instance;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();
    private FusedLocationProviderClient fusedLocationClient;
    private LocationCallback locationCallback;
    private ConnectivityManager.NetworkCallback networkCallback;
    private volatile HttpURLConnection connection;
    private volatile Session session;
    private SchoolHopLocationTracker tracker;
    private long lastFixAt = -1;
    private long lastSuccessAt = -1;
    enum ValidationFailure { NONE, MALFORMED_RESPONSE, INVALID_SAFETY_DEADLINE, TRIP_NOT_ACTIVE, DRIVER_MISMATCH, DEADLINE_EXPIRED }
    private int validationHttpStatus;
    private ValidationFailure validationFailure = ValidationFailure.NONE;

    static final class Session {
        final String tripId;
        final String token;
        final String baseUrl;
        final String userId;
        final long tokenExpiry;

        Session(String tripId, String token, String baseUrl) throws Exception {
            this.tripId = UUID.fromString(tripId).toString();
            if (token == null || token.length() > 16384) throw new IllegalArgumentException();
            String[] parts = token.split("\\.");
            if (parts.length != 3) throw new IllegalArgumentException();
            JSONObject claims = new JSONObject(new String(Base64.decode(parts[1], Base64.URL_SAFE | Base64.NO_WRAP), StandardCharsets.UTF_8));
            this.userId = UUID.fromString(claims.getString("sub")).toString();
            this.tokenExpiry = Math.multiplyExact(claims.getLong("exp"), 1000);
            if (tokenExpiry <= System.currentTimeMillis() || !validBaseUrl(baseUrl)) throw new IllegalArgumentException();
            this.token = token;
            this.baseUrl = baseUrl.replaceAll("/+$", "");
        }

        String endpoint(boolean upload) { return baseUrl + "/api/trips/" + tripId + (upload ? "/locations" : ""); }
    }

    static boolean validBaseUrl(String value) {
        try {
            URI uri = new URI(value);
            return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null
                && uri.getUserInfo() == null && uri.getRawQuery() == null && uri.getRawFragment() == null
                && (uri.getPath().isEmpty() || "/".equals(uri.getPath()));
        } catch (Exception ignored) { return false; }
    }

    static boolean running() { return instance != null && instance.tracker != null && instance.tracker.isRunning(); }

    static boolean requestFreshLocation() {
        SchoolHopLocationService service = instance;
        if (service == null || !running() || !service.tracker.isCollecting()) return false;
        service.mainHandler.post(service::requestFreshFix);
        return true;
    }

    @Override public void onCreate() {
        super.onCreate();
        instance = this;
        fusedLocationClient = createLocationClient();
        tracker = new SchoolHopLocationTracker(new SchoolHopLocationTracker.Host() {
            public long now() { return SystemClock.elapsedRealtime(); }
            public void schedule(Runnable task, long delay) { mainHandler.postDelayed(task, delay); }
            public void cancel(Runnable task) { mainHandler.removeCallbacks(task); }
            public boolean permitted() { return hasLocationPermission(SchoolHopLocationService.this); }
            public void collect(boolean enabled) { setCollection(enabled); }
            public void validate(SchoolHopLocationTracker.ValidationCallback callback) { validateTrip(callback); }
            public void upload(Object fix, SchoolHopLocationTracker.ResultCallback callback) { uploadFix((Location) fix, callback); }
            public void stopped(int status) { finishTracking(status); }
            public void diagnostic(SchoolHopLocationTracker.Event event, int status) {
                if (event == SchoolHopLocationTracker.Event.UPLOADED) lastSuccessAt = now();
                SchoolHopLocationDiagnostics.record(SchoolHopLocationService.this, event, status,
                    tracker.isCollecting(), lastFixAt < 0 ? -1 : now() - lastFixAt,
                    lastSuccessAt < 0 ? -1 : now() - lastSuccessAt, validationHttpStatus, validationFailure);
            }
        });
        registerNetworkCallback();
    }

    // Lint includes undeclared JVM-test subclasses; the concrete service's type is
    // verified in the manifest regression test and passed explicitly at runtime.
    @SuppressLint("ForegroundServiceType")
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            if (tracker.isRunning()) tracker.stop(0); else finishTracking(0);
            return START_NOT_STICKY;
        }
        try {
            boolean restoring = intent == null;
            if (!restoring && !ACTION_START.equals(intent.getAction())) throw new IllegalArgumentException();
            if (restoring && !canRecoverInBackground()) throw new SecurityException();
            SharedPreferences prefs = trackingPreferences();
            if (restoring && !prefs.getBoolean("tracking", false)) throw new IllegalArgumentException();
            Session next = new Session(
                restoring ? prefs.getString("tripId", "") : intent.getStringExtra("tripId"),
                restoring ? prefs.getString("token", "") : intent.getStringExtra("token"),
                restoring ? prefs.getString("apiBaseUrl", "") : intent.getStringExtra("apiBaseUrl"));
            if (!hasLocationPermission(this)) throw new SecurityException();
            ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(),
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q ? ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION : 0);
            if (session != null && tracker.isRunning() && session.tripId.equals(next.tripId)
                    && session.token.equals(next.token) && session.baseUrl.equals(next.baseUrl)) {
                if (tracker.isCollecting()) broadcast(ACTION_TRACKING_READY, 0);
            } else {
                tracker.abandon();
                disconnect();
                session = next;
                lastFixAt = lastSuccessAt = -1;
                validationHttpStatus = 0;
                validationFailure = ValidationFailure.NONE;
                if (!prefs.edit().putBoolean("tracking", true).putString("tripId", next.tripId)
                        .putString("token", next.token).putString("apiBaseUrl", next.baseUrl).commit()) {
                    throw new IllegalStateException();
                }
                // Every start/recovery validates the active trip before collecting GPS.
                tracker.start();
            }
            return canRecoverInBackground() ? START_STICKY : START_NOT_STICKY;
        } catch (Exception ignored) {
            if (tracker.isRunning()) tracker.stop(403); else finishTracking(403);
            return START_NOT_STICKY;
        }
    }

    protected FusedLocationProviderClient createLocationClient() { return LocationServices.getFusedLocationProviderClient(this); }
    protected SharedPreferences trackingPreferences() throws Exception { return SchoolHopSecureStore.preferences(this); }
    protected boolean canRecoverInBackground() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.R || ContextCompat.checkSelfPermission(this,
            Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    static boolean hasLocationPermission(Context context) {
        return ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
            || ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    protected void setCollection(boolean enabled) {
        if (!enabled) {
            if (locationCallback != null) fusedLocationClient.removeLocationUpdates(locationCallback);
            locationCallback = null;
            return;
        }
        if (locationCallback != null) return;
        LocationRequest request = new LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, LOCATION_INTERVAL_MS)
            .setMinUpdateIntervalMillis(LOCATION_INTERVAL_MS)
            .setMinUpdateDistanceMeters(0) // Fresh stationary fixes keep the server timestamp current.
            .setMaxUpdateDelayMillis(0)
            .setMaxUpdateAgeMillis(0)
            .build();
        LocationCallback callback = new LocationCallback() {
            @Override public void onLocationResult(LocationResult result) {
                if (locationCallback != this) return;
                Location fix = result.getLastLocation();
                if (fix != null) acceptFix(fix);
            }
        };
        locationCallback = callback;
        try {
            fusedLocationClient.requestLocationUpdates(request, callback, Looper.getMainLooper())
                .addOnSuccessListener(unused -> {
                    if (locationCallback == callback && tracker.isCollecting()) broadcast(ACTION_TRACKING_READY, 0);
                })
                .addOnFailureListener(error -> {
                    if (locationCallback == callback) tracker.stop(error instanceof SecurityException ? 403 : 0);
                });
        } catch (SecurityException ignored) { tracker.stop(403); }
    }

    private void requestFreshFix() {
        if (!tracker.isCollecting() || !hasLocationPermission(this)) return;
        try {
            Session requested = session;
            fusedLocationClient.getCurrentLocation(new CurrentLocationRequest.Builder()
                .setPriority(Priority.PRIORITY_HIGH_ACCURACY).setMaxUpdateAgeMillis(0).setDurationMillis(10000).build(), null)
                .addOnSuccessListener(fix -> {
                    if (fix != null && session == requested && tracker.isCollecting()) acceptFix(fix);
                });
        } catch (SecurityException ignored) { tracker.stop(403); }
    }

    void acceptFix(Location fix) {
        long capturedAt = fix.getElapsedRealtimeNanos() / 1000000;
        lastFixAt = capturedAt;
        tracker.offer(new Location(fix), capturedAt);
    }

    private void validateTrip(SchoolHopLocationTracker.ValidationCallback callback) {
        Session requested = session;
        runRequest(requested, false, null, (status, body) -> {
            long deadline = 0;
            validationHttpStatus = status;
            validationFailure = ValidationFailure.NONE;
            if (status == 200) {
                try {
                    JSONObject trip = new JSONObject(body);
                    long safetyUntil = SchoolHopSafetyDeadline.parseMillis(trip.getString("safety_timeout_at"));
                    boolean active = "started".equals(trip.getString("status"))
                        || ("delayed".equals(trip.getString("status")) && !trip.isNull("started_at"));
                    if (!active || !trip.isNull("ended_at")) {
                        validationFailure = ValidationFailure.TRIP_NOT_ACTIVE;
                    } else if (!requested.userId.equals(trip.getString("driver_user_id"))) {
                        validationFailure = ValidationFailure.DRIVER_MISMATCH;
                    }
                    long remaining = Math.min(safetyUntil, requested.tokenExpiry) - System.currentTimeMillis();
                    if (remaining <= 0 && validationFailure == ValidationFailure.NONE) {
                        validationFailure = ValidationFailure.DEADLINE_EXPIRED;
                    }
                    if (validationFailure != ValidationFailure.NONE) status = 409;
                    deadline = SystemClock.elapsedRealtime() + Math.max(0, remaining);
                } catch (DateTimeException | ArithmeticException ignored) {
                    validationFailure = ValidationFailure.INVALID_SAFETY_DEADLINE;
                    status = 409;
                } catch (Exception ignored) {
                    validationFailure = ValidationFailure.MALFORMED_RESPONSE;
                    status = 409;
                }
            }
            callback.complete(status, deadline);
        });
    }

    private void uploadFix(Location fix, SchoolHopLocationTracker.ResultCallback callback) {
        runRequest(session, true, locationPayload(fix), (status, ignored) -> callback.complete(status));
    }

    interface HttpCallback { void complete(int status, String body); }
    static final class Reply {
        final int status;
        final String body;
        Reply(int status, String body) { this.status = status; this.body = body; }
    }

    protected void executeNetwork(Runnable job) { networkExecutor.execute(job); }

    private void runRequest(Session requested, boolean upload, String payload, HttpCallback callback) {
        // GMS holds a lock during delivery, not necessarily through this asynchronous upload.
        PowerManager.WakeLock wake = getSystemService(PowerManager.class).newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK, "schoolhop:active-trip-request");
        wake.acquire(20000);
        try {
            executeNetwork(() -> {
                Reply reply;
                try {
                    if (session != requested || !tracker.isRunning()) {
                        if (wake.isHeld()) wake.release();
                        return;
                    }
                    reply = performRequest(requested, upload, payload);
                } catch (Exception ignored) { reply = new Reply(0, ""); }
                Reply completed = reply;
                mainHandler.post(() -> {
                    try {
                        if (session == requested && tracker.isRunning()) callback.complete(completed.status, completed.body);
                    } finally { if (wake.isHeld()) wake.release(); }
                });
            });
        } catch (RuntimeException ignored) {
            if (wake.isHeld()) wake.release();
            mainHandler.post(() -> { if (session == requested && tracker.isRunning()) callback.complete(0, ""); });
        }
    }

    protected Reply performRequest(Session requested, boolean upload, String payload) throws Exception {
        HttpURLConnection request = openConnection(requested.endpoint(upload));
        connection = request;
        try {
            request.setUseCaches(false);
            request.setRequestProperty("Cache-Control", "no-store");
            request.setInstanceFollowRedirects(false); // Never forward bearer credentials to redirects.
            request.setRequestMethod(upload ? "POST" : "GET");
            request.setConnectTimeout(8000);
            request.setReadTimeout(8000);
            request.setRequestProperty("Authorization", "Bearer " + requested.token);
            if (upload) {
                request.setRequestProperty("Content-Type", "application/json");
                request.setDoOutput(true);
                byte[] body = payload.getBytes(StandardCharsets.UTF_8);
                request.setFixedLengthStreamingMode(body.length);
                try (OutputStream output = request.getOutputStream()) { output.write(body); }
            }
            int status = request.getResponseCode();
            String body = "";
            if (!upload && status == 200) {
                try (InputStream input = request.getInputStream()) {
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                    byte[] buffer = new byte[4096];
                    int count;
                    while ((count = input.read(buffer)) != -1) {
                        if (bytes.size() + count > 65536) return new Reply(409, "");
                        bytes.write(buffer, 0, count);
                    }
                    body = bytes.toString(StandardCharsets.UTF_8.name());
                }
            }
            return new Reply(status, body);
        } finally {
            request.disconnect();
            if (connection == request) connection = null;
        }
    }

    protected HttpURLConnection openConnection(String url) throws Exception {
        return (HttpURLConnection) new URI(url).toURL().openConnection();
    }

    static String locationPayload(Location location) {
        return String.format(Locale.US,
            "{\"latitude\":%.7f,\"longitude\":%.7f,\"accuracy_meters\":%.2f,\"speed_mps\":%s,\"heading_degrees\":%s,\"recorded_at\":\"%s\"}",
            location.getLatitude(), location.getLongitude(), Math.max(location.getAccuracy(), 0),
            location.hasSpeed() ? String.format(Locale.US, "%.3f", Math.max(location.getSpeed(), 0)) : "null",
            location.hasBearing() ? String.format(Locale.US, "%.3f", location.getBearing()) : "null",
            Instant.ofEpochMilli(location.getTime()).toString());
    }

    private Notification buildNotification() {
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, getString(R.string.location_channel_name), NotificationManager.IMPORTANCE_LOW);
            channel.setDescription(getString(R.string.location_channel_description));
            manager.createNotificationChannel(channel);
        }
        PendingIntent open = PendingIntent.getActivity(this, 0,
            new Intent(this, MainActivity.class).setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1,
            new Intent(this, SchoolHopLocationService.class).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(getApplicationInfo().icon)
            .setContentTitle(getString(R.string.location_notification_title))
            .setContentText(getString(R.string.location_notification_body))
            .setContentIntent(open).setOngoing(true).setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW).setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(0, getString(R.string.location_stop_sharing), stop).build();
    }

    private void registerNetworkCallback() {
        if (Build.VERSION.SDK_INT < 24) return; // Periodic native validation still recovers on API 23.
        networkCallback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) { mainHandler.post(() -> { if (tracker != null) tracker.networkAvailable(); }); }
        };
        try { getSystemService(ConnectivityManager.class).registerDefaultNetworkCallback(networkCallback); }
        catch (RuntimeException ignored) { networkCallback = null; }
    }

    @SuppressLint("ApplySharedPref") // Persist the stop before a process death can restore old credentials.
    private void finishTracking(int status) {
        try { trackingPreferences().edit().clear().commit(); }
        catch (Exception ignored) { /* The service still stops if secure storage fails. */ }
        broadcast(ACTION_TRACKING_STOPPED, status);
        session = null;
        disconnect();
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    @SuppressLint("ApplySharedPref") // Explicit stop/logout must synchronously invalidate recovery state.
    static void clearSavedState(Context context) {
        try { SchoolHopSecureStore.preferences(context).edit().clear().commit(); }
        catch (Exception ignored) { /* A missing key must never keep location collection running. */ }
    }

    protected void broadcast(String action, int status) {
        Intent intent = new Intent(action).setPackage(getPackageName()).putExtra("status", status);
        if (session != null) intent.putExtra("tripId", session.tripId);
        sendBroadcast(intent);
    }

    private void disconnect() {
        HttpURLConnection active = connection;
        connection = null;
        if (active != null) active.disconnect();
    }

    @Nullable @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        // System destruction may preserve encrypted intent to recover. Explicit stops clear it first.
        if (tracker != null) tracker.abandon();
        session = null;
        disconnect();
        mainHandler.removeCallbacksAndMessages(null);
        if (networkCallback != null) getSystemService(ConnectivityManager.class).unregisterNetworkCallback(networkCallback);
        networkExecutor.shutdownNow();
        if (instance == this) instance = null;
        super.onDestroy();
    }
}
