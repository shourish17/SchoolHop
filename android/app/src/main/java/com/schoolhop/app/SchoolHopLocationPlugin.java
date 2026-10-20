package com.schoolhop.app;

import android.Manifest;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.os.Handler;
import android.os.Looper;
import androidx.core.content.ContextCompat;
import androidx.lifecycle.Lifecycle;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

@CapacitorPlugin(name = "SchoolHopLocation", permissions = {
    @Permission(strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION }, alias = "foregroundLocation")
})
public class SchoolHopLocationPlugin extends Plugin {
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private PluginCall pendingStartCall;
    private String pendingTripId;
    private final Runnable startTimeout = () -> failStart("GPS could not start. Open SchoolHop, check location/network access and try again.");

    private final BroadcastReceiver trackingReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) {
            JSObject payload = new JSObject();
            payload.put("status", intent.getIntExtra("status", 0));
            payload.put("tripId", intent.getStringExtra("tripId"));
            if (SchoolHopLocationService.ACTION_TRACKING_READY.equals(intent.getAction())) {
                if (pendingStartCall != null && pendingTripId.equals(intent.getStringExtra("tripId"))) {
                    PluginCall call = pendingStartCall;
                    mainHandler.removeCallbacks(startTimeout);
                    pendingStartCall = null;
                    pendingTripId = null;
                    JSObject result = statusPayload(true, intent.getStringExtra("tripId"));
                    result.put("status", "tracking");
                    call.resolve(result);
                }
                return;
            }
            boolean stopped = SchoolHopLocationService.ACTION_TRACKING_STOPPED.equals(intent.getAction());
            if (stopped && pendingStartCall != null) failStart("Active-trip location tracking could not start");
            notifyListeners(stopped ? "trackingStopped" : "trackingError", payload, true);
        }
    };

    @Override public void load() {
        IntentFilter filter = new IntentFilter();
        filter.addAction(SchoolHopLocationService.ACTION_TRACKING_READY);
        filter.addAction(SchoolHopLocationService.ACTION_TRACKING_STOPPED);
        filter.addAction(SchoolHopLocationService.ACTION_TRACKING_ERROR);
        ContextCompat.registerReceiver(getContext(), trackingReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED);
    }

    @PluginMethod public void startTripTracking(PluginCall call) {
        mainHandler.post(() -> {
            if (!visible()) { call.reject("Open SchoolHop before starting GPS sharing"); return; }
            if (pendingStartCall != null) { call.reject("GPS startup is already in progress"); return; }
            try {
                SchoolHopLocationService.Session next = new SchoolHopLocationService.Session(
                    call.getString("tripId", ""), call.getString("token", ""), call.getString("apiBaseUrl", ""));
                pendingStartCall = call;
                pendingTripId = next.tripId;
            } catch (Exception ignored) {
                call.reject("SchoolHop Android tracking requires an HTTPS API base URL and valid trip credentials");
                return;
            }
            if (!SchoolHopLocationService.hasLocationPermission(getContext())) {
                requestPermissionForAlias("foregroundLocation", call, "foregroundPermissionCallback");
                return;
            }
            startServiceAfterPermission(call);
        });
    }

    @PermissionCallback private void foregroundPermissionCallback(PluginCall call) {
        mainHandler.post(() -> {
            if (pendingStartCall != call) return;
            // Android 12 approximate permission grants COARSE, not FINE. Either is usable.
            if (!SchoolHopLocationService.hasLocationPermission(getContext())) {
                failStart("Location permission is required for active driver trip tracking");
                return;
            }
            startServiceAfterPermission(call);
        });
    }

    private boolean visible() {
        return getActivity() != null && !getActivity().isFinishing() && !getActivity().isDestroyed()
            && getActivity().getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED);
    }

    private void startServiceAfterPermission(PluginCall call) {
        if (!visible()) { failStart("Open SchoolHop before starting GPS sharing"); return; }
        try {
            Intent intent = new Intent(getContext(), SchoolHopLocationService.class)
                .setAction(SchoolHopLocationService.ACTION_START)
                .putExtra("tripId", call.getString("tripId", ""))
                .putExtra("token", call.getString("token", ""))
                .putExtra("apiBaseUrl", call.getString("apiBaseUrl", ""));
            ContextCompat.startForegroundService(getContext(), intent);
            mainHandler.postDelayed(startTimeout, 30000);
            // Resolve only after the foreground service validates the trip and subscribes to GPS.
        } catch (RuntimeException ignored) {
            failStart("Android could not start GPS sharing. Keep SchoolHop open and try again.");
        }
    }

    private void failStart(String message) {
        mainHandler.removeCallbacks(startTimeout);
        PluginCall call = pendingStartCall;
        pendingStartCall = null;
        pendingTripId = null;
        if (call != null) {
            SchoolHopLocationService.clearSavedState(getContext());
            getContext().stopService(new Intent(getContext(), SchoolHopLocationService.class));
            call.reject(message);
            notifyListeners("trackingStopped", statusPayload(false, ""), true);
        }
    }

    @PluginMethod public void stopTripTracking(PluginCall call) {
        mainHandler.post(() -> {
            failStart("GPS sharing was stopped");
            SchoolHopLocationService.clearSavedState(getContext());
            // Never cold-start a foreground service merely to stop it, especially after lock/logout.
            getContext().stopService(new Intent(getContext(), SchoolHopLocationService.class));
            call.resolve(statusPayload(false, ""));
        });
    }

    @PluginMethod public void requestLocationUpdate(PluginCall call) {
        if (!SchoolHopLocationService.requestFreshLocation()) { call.reject("Start active-trip GPS sharing first"); return; }
        call.resolve();
    }

    @PluginMethod public void status(PluginCall call) {
        try {
            SharedPreferences prefs = SchoolHopSecureStore.preferences(getContext());
            call.resolve(statusPayload(SchoolHopLocationService.running() && prefs.getBoolean("tracking", false), prefs.getString("tripId", "")));
        } catch (Exception ignored) { call.resolve(statusPayload(false, "")); }
    }

    @PluginMethod public void getTrackingDiagnostics(PluginCall call) {
        if (!SchoolHopLocationDiagnostics.isEnabled()) { call.unavailable("Location diagnostics are available only in debug builds"); return; }
        call.resolve(SchoolHopLocationDiagnostics.snapshot(getContext()));
    }

    private JSObject statusPayload(boolean tracking, String tripId) {
        JSObject result = new JSObject();
        result.put("tracking", tracking);
        result.put("tripId", tripId == null ? "" : tripId);
        result.put("authorization", SchoolHopLocationService.hasLocationPermission(getContext()) ? "granted" : "denied");
        return result;
    }

    @Override protected void handleOnDestroy() {
        failStart("GPS startup was cancelled when the activity closed");
        mainHandler.removeCallbacksAndMessages(null);
        try { getContext().unregisterReceiver(trackingReceiver); }
        catch (IllegalArgumentException ignored) { /* Already released by the runtime. */ }
        // An established active service deliberately outlives the WebView/activity.
    }
}
