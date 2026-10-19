package com.schoolhop.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "SchoolHopConfig")
public class SchoolHopConfigPlugin extends Plugin {
    @PluginMethod
    public void getNotificationDiagnostics(PluginCall call) {
        if (!SchoolHopPushDiagnostics.isEnabled()) {
            call.unavailable("Notification diagnostics are available only in debug builds");
            return;
        }
        call.resolve(SchoolHopPushDiagnostics.snapshot(getContext()));
    }

    @PluginMethod
    public void getFirebaseMessagingStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("configured", SchoolHopFirebaseConfig.hasFirebaseMessagingConfig(getContext()));
        call.resolve(result);
    }
}
