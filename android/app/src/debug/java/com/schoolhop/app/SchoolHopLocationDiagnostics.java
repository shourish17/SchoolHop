package com.schoolhop.app;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.location.LocationManager;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.PowerManager;
import android.util.Log;
import androidx.core.content.ContextCompat;
import androidx.core.app.NotificationManagerCompat;
import com.getcapacitor.JSObject;

/** Fixed metadata only. Never accepts identifiers, URLs, payloads or exceptions. */
final class SchoolHopLocationDiagnostics {
    private static SchoolHopLocationTracker.Event lastEvent;
    private static int lastStatus;
    private static boolean collecting;
    private static long fixAge = -1;
    private static long uploadAge = -1;

    static boolean isEnabled() { return true; }

    static synchronized void record(Context context, SchoolHopLocationTracker.Event event, int status,
            boolean active, long fixAgeMs, long uploadAgeMs) {
        lastEvent = event;
        lastStatus = status;
        collecting = active;
        fixAge = fixAgeMs;
        uploadAge = uploadAgeMs;
        Log.i("SchoolHopGpsDebug", snapshot(context).toString());
    }

    static synchronized JSObject snapshot(Context context) {
        PowerManager power = context.getSystemService(PowerManager.class);
        JSObject result = new JSObject();
        result.put("debugOnly", true);
        result.put("event", lastEvent == null ? "NONE" : lastEvent.name());
        result.put("httpStatus", lastStatus);
        result.put("tracking", SchoolHopLocationService.running());
        result.put("collecting", collecting);
        result.put("fixAgeMsAtEvent", fixAge);
        result.put("uploadAgeMsAtEvent", uploadAge);
        result.put("interactive", power.isInteractive());
        result.put("powerSave", power.isPowerSaveMode());
        result.put("idle", power.isDeviceIdleMode());
        result.put("batteryExempt", power.isIgnoringBatteryOptimizations(context.getPackageName()));
        result.put("fineLocation", granted(context, Manifest.permission.ACCESS_FINE_LOCATION));
        result.put("coarseLocation", granted(context, Manifest.permission.ACCESS_COARSE_LOCATION));
        result.put("backgroundLocation", Build.VERSION.SDK_INT < 29 || granted(context, Manifest.permission.ACCESS_BACKGROUND_LOCATION));
        result.put("notificationsEnabled", NotificationManagerCompat.from(context).areNotificationsEnabled());
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel channel = context.getSystemService(NotificationManager.class)
                .getNotificationChannel(SchoolHopLocationService.CHANNEL_ID);
            result.put("locationChannelExists", channel != null);
            result.put("locationChannelImportance", channel == null ? -1 : channel.getImportance());
        }
        LocationManager location = context.getSystemService(LocationManager.class);
        result.put("locationEnabled", Build.VERSION.SDK_INT >= 28 ? location.isLocationEnabled()
            : location.isProviderEnabled(LocationManager.GPS_PROVIDER) || location.isProviderEnabled(LocationManager.NETWORK_PROVIDER));
        ConnectivityManager network = context.getSystemService(ConnectivityManager.class);
        NetworkCapabilities capabilities = network.getNetworkCapabilities(network.getActiveNetwork());
        result.put("networkValidated", capabilities != null && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED));
        return result;
    }

    private static boolean granted(Context context, String permission) {
        return ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED;
    }
}
