package com.schoolhop.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.location.Location;
import android.os.Build;
import android.os.IBinder;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.content.ContextCompat;
import com.google.android.gms.location.FusedLocationProviderClient;
import com.google.android.gms.location.LocationCallback;
import com.google.android.gms.location.LocationRequest;
import com.google.android.gms.location.LocationResult;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.Priority;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class SchoolHopLocationService extends Service {
    static final String ACTION_START = "com.schoolhop.app.location.START";
    static final String ACTION_STOP = "com.schoolhop.app.location.STOP";
    static final String ACTION_TRACKING_STOPPED = "com.schoolhop.app.location.TRACKING_STOPPED";
    static final String ACTION_TRACKING_ERROR = "com.schoolhop.app.location.TRACKING_ERROR";
    private static final String CHANNEL_ID = "schoolhop_active_trip_location";
    private static final int NOTIFICATION_ID = 17017;
    private static final long POST_THROTTLE_MS = 4000L;

    private FusedLocationProviderClient fusedLocationClient;
    private final ExecutorService networkExecutor = Executors.newSingleThreadExecutor();
    private LocationCallback locationCallback;
    private String tripId;
    private String token;
    private String apiBaseUrl;
    private long lastPostAt;
    private boolean postInFlight;
    private Location queuedLocation;

    @Override
    public void onCreate() {
        super.onCreate();
        fusedLocationClient = LocationServices.getFusedLocationProviderClient(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String action = intent == null ? null : intent.getAction();
        if (ACTION_STOP.equals(action)) {
            stopTracking(0, null);
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(action)) {
            tripId = intent.getStringExtra("tripId");
            token = intent.getStringExtra("token");
            apiBaseUrl = intent.getStringExtra("apiBaseUrl");
            persistTrackingState();
        } else {
            restoreTrackingState();
        }
        if (tripId == null || token == null || apiBaseUrl == null || !hasFineLocationPermission()) {
            stopTracking(403, "Location permission or trip state is unavailable");
            return START_NOT_STICKY;
        }
        startForeground(NOTIFICATION_ID, buildNotification());
        startLocationUpdates();
        return START_STICKY;
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        if (locationCallback != null) fusedLocationClient.removeLocationUpdates(locationCallback);
        networkExecutor.shutdown();
        super.onDestroy();
    }

    private void startLocationUpdates() {
        if (locationCallback != null) return;
        LocationRequest request = new LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, POST_THROTTLE_MS)
            .setMinUpdateDistanceMeters(25)
            .setMinUpdateIntervalMillis(POST_THROTTLE_MS)
            .build();
        locationCallback = new LocationCallback() {
            @Override
            public void onLocationResult(LocationResult locationResult) {
                Location location = locationResult.getLastLocation();
                if (location != null) queueLocation(location);
            }
        };
        try {
            fusedLocationClient.requestLocationUpdates(request, locationCallback, getMainLooper());
        } catch (SecurityException ex) {
            stopTracking(403, "Location permission was revoked");
        }
    }

    private void queueLocation(Location location) {
        long now = System.currentTimeMillis();
        if (postInFlight || now - lastPostAt < POST_THROTTLE_MS) {
            queuedLocation = location;
            return;
        }
        sendLocation(location);
    }

    private void sendLocation(Location location) {
        postInFlight = true;
        lastPostAt = System.currentTimeMillis();
        networkExecutor.execute(() -> {
            int status = 0;
            String errorMessage = null;
            try {
                URL url = new URL(apiBaseUrl + "/api/trips/" + tripId + "/locations");
                HttpURLConnection connection = (HttpURLConnection) url.openConnection();
                connection.setRequestMethod("POST");
                connection.setConnectTimeout(8000);
                connection.setReadTimeout(8000);
                connection.setRequestProperty("Content-Type", "application/json");
                connection.setRequestProperty("Authorization", "Bearer " + token);
                connection.setDoOutput(true);
                byte[] body = locationPayload(location).getBytes(StandardCharsets.UTF_8);
                try (OutputStream output = connection.getOutputStream()) {
                    output.write(body);
                }
                status = connection.getResponseCode();
                connection.disconnect();
            } catch (Exception ex) {
                errorMessage = "Location update failed";
            }
            int finalStatus = status;
            String finalErrorMessage = errorMessage;
            getMainExecutor().execute(() -> onPostComplete(finalStatus, finalErrorMessage));
        });
    }

    private void onPostComplete(int status, String errorMessage) {
        postInFlight = false;
        if (status == 401 || status == 403 || status == 404 || status == 409) {
            stopTracking(status, null);
            return;
        }
        if (errorMessage != null) broadcast(ACTION_TRACKING_ERROR, 0, errorMessage);
        if (queuedLocation != null) {
            Location next = queuedLocation;
            queuedLocation = null;
            queueLocation(next);
        }
    }

    private String locationPayload(Location location) {
        String recordedAt = DateTimeFormatter.ISO_INSTANT.format(Instant.ofEpochMilli(location.getTime()));
        String speed = location.hasSpeed() ? String.format(Locale.US, "%.3f", location.getSpeed()) : "null";
        String bearing = location.hasBearing() ? String.format(Locale.US, "%.3f", location.getBearing()) : "null";
        return String.format(
            Locale.US,
            "{\"latitude\":%.7f,\"longitude\":%.7f,\"accuracy_meters\":%.2f,\"speed_mps\":%s,\"heading_degrees\":%s,\"recorded_at\":\"%s\"}",
            location.getLatitude(),
            location.getLongitude(),
            Math.max(location.getAccuracy(), 0),
            speed,
            bearing,
            recordedAt
        );
    }

    private Notification buildNotification() {
        createNotificationChannel();
        Intent launchIntent = new Intent(this, MainActivity.class);
        launchIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this,
            0,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );
        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(getApplicationInfo().icon)
            .setContentTitle(getString(R.string.location_notification_title))
            .setContentText(getString(R.string.location_notification_body))
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build();
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return;
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        NotificationChannel channel = new NotificationChannel(CHANNEL_ID, getString(R.string.location_channel_name), NotificationManager.IMPORTANCE_LOW);
        channel.setDescription(getString(R.string.location_channel_description));
        manager.createNotificationChannel(channel);
    }

    private boolean hasFineLocationPermission() {
        return ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void persistTrackingState() {
        try {
            SchoolHopSecureStore.preferences(this).edit()
                .putBoolean("tracking", true)
                .putString("tripId", tripId)
                .putString("token", token)
                .putString("apiBaseUrl", apiBaseUrl)
                .apply();
        } catch (Exception ex) {
            stopTracking(0, "Unable to persist active trip tracking state");
        }
    }

    private void restoreTrackingState() {
        try {
            SharedPreferences prefs = SchoolHopSecureStore.preferences(this);
            if (!prefs.getBoolean("tracking", false)) return;
            tripId = prefs.getString("tripId", null);
            token = prefs.getString("token", null);
            apiBaseUrl = prefs.getString("apiBaseUrl", null);
        } catch (Exception ignored) {
            // Missing state is handled by the caller as an inactive service.
        }
    }

    private void clearTrackingState() {
        try {
            SchoolHopSecureStore.preferences(this).edit().clear().apply();
        } catch (Exception ignored) {
            // Clearing best-effort state must not keep the foreground service alive.
        }
    }

    private void stopTracking(int status, String message) {
        if (locationCallback != null) {
            fusedLocationClient.removeLocationUpdates(locationCallback);
            locationCallback = null;
        }
        clearTrackingState();
        broadcast(ACTION_TRACKING_STOPPED, status, message);
        stopForeground(STOP_FOREGROUND_REMOVE);
        stopSelf();
    }

    private void broadcast(String action, int status, String message) {
        Intent intent = new Intent(action).setPackage(getPackageName()).putExtra("status", status);
        if (message != null) intent.putExtra("message", message);
        sendBroadcast(intent);
    }
}
