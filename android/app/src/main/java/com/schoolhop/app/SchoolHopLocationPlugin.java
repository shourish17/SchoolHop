package com.schoolhop.app;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import androidx.core.content.ContextCompat;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

@CapacitorPlugin(
    name = "SchoolHopLocation",
    permissions = {
        @Permission(
            strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION },
            alias = SchoolHopLocationPlugin.FOREGROUND_LOCATION
        ),
        @Permission(strings = { Manifest.permission.ACCESS_BACKGROUND_LOCATION }, alias = SchoolHopLocationPlugin.BACKGROUND_LOCATION)
    }
)
public class SchoolHopLocationPlugin extends Plugin {
    static final String FOREGROUND_LOCATION = "foregroundLocation";
    static final String BACKGROUND_LOCATION = "backgroundLocation";
    private PluginCall pendingStartCall;

    private final BroadcastReceiver trackingReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            JSObject payload = new JSObject();
            payload.put("status", intent.getIntExtra("status", 0));
            String message = intent.getStringExtra("message");
            if (message != null) payload.put("message", message);
            String event = SchoolHopLocationService.ACTION_TRACKING_STOPPED.equals(intent.getAction()) ? "trackingStopped" : "trackingError";
            notifyListeners(event, payload, true);
        }
    };

    @Override
    public void load() {
        IntentFilter filter = new IntentFilter();
        filter.addAction(SchoolHopLocationService.ACTION_TRACKING_STOPPED);
        filter.addAction(SchoolHopLocationService.ACTION_TRACKING_ERROR);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            getContext().registerReceiver(trackingReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            getContext().registerReceiver(trackingReceiver, filter);
        }
    }

    @PluginMethod
    public void startTripTracking(PluginCall call) {
        String tripId = call.getString("tripId", "");
        String token = call.getString("token", "");
        String apiBaseUrl = call.getString("apiBaseUrl", "");
        if (tripId.isEmpty()) {
            call.reject("Missing tripId");
            return;
        }
        if (token.isEmpty()) {
            call.reject("Missing bearer token");
            return;
        }
        if (!isHttpsApiBaseUrl(apiBaseUrl)) {
            call.reject("SchoolHop Android tracking requires an HTTPS API base URL");
            return;
        }
        pendingStartCall = call;
        if (getPermissionState(FOREGROUND_LOCATION) != PermissionState.GRANTED) {
            requestPermissionForAlias(FOREGROUND_LOCATION, call, "foregroundPermissionCallback");
            return;
        }
        startServiceAfterPermission(call, tripId, token, apiBaseUrl);
    }

    @PermissionCallback
    private void foregroundPermissionCallback(PluginCall call) {
        if (getPermissionState(FOREGROUND_LOCATION) != PermissionState.GRANTED) {
            call.reject("Location permission is required for active driver trip tracking");
            pendingStartCall = null;
            return;
        }
        startServiceAfterPermission(call, call.getString("tripId", ""), call.getString("token", ""), call.getString("apiBaseUrl", ""));
    }

    @PluginMethod
    public void stopTripTracking(PluginCall call) {
        ContextCompat.startForegroundService(getContext(), new Intent(getContext(), SchoolHopLocationService.class).setAction(SchoolHopLocationService.ACTION_STOP));
        call.resolve(statusPayload(false, ""));
    }

    @PluginMethod
    public void status(PluginCall call) {
        try {
            SharedPreferences prefs = SchoolHopSecureStore.preferences(getContext());
            call.resolve(statusPayload(prefs.getBoolean("tracking", false), prefs.getString("tripId", "")));
        } catch (Exception ex) {
            call.resolve(statusPayload(false, ""));
        }
    }

    private void startServiceAfterPermission(PluginCall call, String tripId, String token, String apiBaseUrl) {
        boolean backgroundGranted = Build.VERSION.SDK_INT < Build.VERSION_CODES.Q ||
            ContextCompat.checkSelfPermission(getContext(), Manifest.permission.ACCESS_BACKGROUND_LOCATION) == PackageManager.PERMISSION_GRANTED;
        Intent intent = new Intent(getContext(), SchoolHopLocationService.class)
            .setAction(SchoolHopLocationService.ACTION_START)
            .putExtra("tripId", tripId)
            .putExtra("token", token)
            .putExtra("apiBaseUrl", apiBaseUrl.replaceAll("/+$", ""));
        ContextCompat.startForegroundService(getContext(), intent);
        JSObject result = statusPayload(true, tripId);
        result.put("status", backgroundGranted ? "tracking" : "tracking_when_in_use");
        result.put("backgroundPermission", backgroundGranted ? "granted" : "prompt");
        call.resolve(result);
        pendingStartCall = null;
    }

    private JSObject statusPayload(boolean tracking, String tripId) {
        JSObject result = new JSObject();
        result.put("tracking", tracking);
        result.put("tripId", tripId == null ? "" : tripId);
        result.put("authorization", getPermissionState(FOREGROUND_LOCATION).toString().toLowerCase());
        return result;
    }

    private boolean isHttpsApiBaseUrl(String value) {
        Uri uri = Uri.parse(value == null ? "" : value);
        return "https".equalsIgnoreCase(uri.getScheme()) && uri.getHost() != null;
    }

    @Override
    protected void handleOnDestroy() {
        try {
            getContext().unregisterReceiver(trackingReceiver);
        } catch (IllegalArgumentException ignored) {
            // Receiver was not registered or was already released by the Android runtime.
        }
        pendingStartCall = null;
    }
}
