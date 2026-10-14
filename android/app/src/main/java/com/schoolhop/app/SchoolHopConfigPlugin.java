package com.schoolhop.app;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

@CapacitorPlugin(name = "SchoolHopConfig")
public class SchoolHopConfigPlugin extends Plugin {
    @PluginMethod
    public void getFirebaseMessagingStatus(PluginCall call) {
        JSObject result = new JSObject();
        result.put("configured", SchoolHopFirebaseConfig.hasFirebaseMessagingConfig(getContext()));
        call.resolve(result);
    }
}
