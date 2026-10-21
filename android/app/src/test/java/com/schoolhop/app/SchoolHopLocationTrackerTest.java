package com.schoolhop.app;

import static org.junit.Assert.*;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.PriorityQueue;
import org.junit.Before;
import org.junit.Test;

public class SchoolHopLocationTrackerTest {
    private Fake host;
    private SchoolHopLocationTracker tracker;

    @Before public void setup() { host = new Fake(); tracker = new SchoolHopLocationTracker(host); }

    @Test public void concreteServiceDeclaresPrivateLocationForegroundContract() throws Exception {
        javax.xml.parsers.DocumentBuilderFactory factory = javax.xml.parsers.DocumentBuilderFactory.newInstance();
        factory.setNamespaceAware(true);
        factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
        org.w3c.dom.Document manifest = factory.newDocumentBuilder().parse(new java.io.File("src/main/AndroidManifest.xml"));
        org.w3c.dom.NodeList services = manifest.getElementsByTagName("service");
        String android = "http://schemas.android.com/apk/res/android";
        boolean found = false;
        for (int i = 0; i < services.getLength(); i++) {
            org.w3c.dom.Element service = (org.w3c.dom.Element) services.item(i);
            if (!".SchoolHopLocationService".equals(service.getAttributeNS(android, "name"))) continue;
            found = true;
            assertEquals("location", service.getAttributeNS(android, "foregroundServiceType"));
            assertEquals("false", service.getAttributeNS(android, "exported"));
            assertEquals("false", service.getAttributeNS(android, "stopWithTask"));
        }
        assertTrue(found);
    }

    @Test public void collectionWaitsForServerValidation() {
        host.autoValidate = false;
        tracker.start();
        assertFalse(host.collecting);
        assertEquals(1, host.validations);
        host.validation.complete(200, host.clock + 3600000);
        assertTrue(host.collecting);
    }

    @Test public void deniedPermissionNeverCollectsOrUploads() {
        host.permission = false;
        tracker.start();
        assertFalse(tracker.isRunning());
        assertEquals(403, host.stopped);
        assertEquals(0, host.validations);
    }

    @Test public void nativeTimersContinueWithoutWebViewOrScreenEvents() {
        tracker.start();
        for (int i = 0; i < 40; i++) {
            tracker.offer("fresh-" + i, host.clock);
            host.advance(10000);
        }
        assertEquals(40, host.uploads.size());
        assertTrue(host.validations >= 13);
        assertTrue(host.collecting);
    }

    @Test public void stationaryCoordinatesWithNewCaptureTimeAreUploadedAgain() {
        tracker.start();
        tracker.offer("same-stationary-coordinates", host.clock);
        host.advance(10000);
        tracker.offer("same-stationary-coordinates", host.clock);
        assertEquals(2, host.uploads.size());
    }

    @Test public void latestQueuedFixDrainsWithoutAnotherGpsCallback() {
        tracker.start();
        tracker.offer("first", host.clock);
        host.advance(1000); tracker.offer("superseded", host.clock);
        host.advance(1000); tracker.offer("latest", host.clock);
        host.advance(2000);
        assertEquals(List.of("first", "latest"), host.uploads);
    }

    @Test public void oneUploadAtATimeAndDuplicateCaptureIsSuppressed() {
        host.autoUpload = false;
        tracker.start();
        long firstTime = host.clock;
        tracker.offer("first", firstTime);
        host.advance(5000);
        tracker.offer("duplicate", firstTime);
        tracker.offer("newer", host.clock);
        assertEquals(1, host.uploads.size());
        host.upload.complete(200);
        assertEquals(List.of("first", "newer"), host.uploads);
    }

    @Test public void failureNeverReplaysAnAmbiguouslyAcceptedFix() {
        tracker.start();
        host.uploadStatus = 0;
        long capturedAt = host.clock;
        tracker.offer("ambiguous", capturedAt);
        host.advance(6000);
        tracker.offer("same-fix", capturedAt);
        assertEquals(List.of("ambiguous"), host.uploads);
        host.uploadStatus = 200;
        tracker.offer("new-fix", host.clock);
        assertEquals(List.of("ambiguous", "new-fix"), host.uploads);
    }

    @Test public void outageCoalescesAndRecoveryValidatesBeforeSendingLatest() {
        tracker.start();
        host.uploadStatus = 503;
        tracker.offer("failed", host.clock);
        host.advance(1000); tracker.offer("older-pending", host.clock);
        host.advance(1000); tracker.offer("latest-pending", host.clock);
        host.autoValidate = false;
        tracker.networkAvailable();
        host.advance(5000);
        assertEquals(1, host.uploads.size());
        host.uploadStatus = 200;
        host.validation.complete(200, host.clock + 3600000);
        assertEquals(List.of("failed", "latest-pending"), host.uploads);
    }

    @Test public void staleAndFutureDatedFixesAreNeverUploaded() {
        tracker.start();
        tracker.offer("old", host.clock - 30001);
        tracker.offer("future", host.clock + 1);
        assertTrue(host.uploads.isEmpty());
    }

    @Test public void prolongedOfflineStatePausesGpsAndResumesOnlyAfterValidation() {
        tracker.start();
        host.validateStatus = 0;
        host.advance(120000);
        assertFalse(host.collecting);
        tracker.offer("offline", host.clock);
        assertTrue(host.uploads.isEmpty());
        host.validateStatus = 200;
        tracker.networkAvailable();
        assertTrue(host.collecting);
    }

    @Test public void failedInitialValidationIsBoundedWithoutCollecting() {
        host.validateStatus = 503;
        tracker.start();
        host.advance(120000);
        assertFalse(tracker.isRunning());
        assertFalse(host.collecting);
    }

    @Test public void serverCompletionStopsWithoutAnyGpsFix() {
        tracker.start();
        host.validateStatus = 409;
        host.advance(30000);
        assertFalse(tracker.isRunning());
        assertFalse(host.collecting);
        assertEquals(409, host.stopped);
    }

    @Test public void tokenExpiryOrSafetyDeadlineStopsDuringNetworkLoss() {
        host.lifetime = 45000;
        tracker.start();
        host.validateStatus = 0;
        host.advance(45000);
        assertFalse(tracker.isRunning());
        assertFalse(host.collecting);
    }

    @Test public void revokedLocationPermissionStopsAtNativePulse() {
        tracker.start(); host.permission = false; host.advance(1000);
        assertEquals(403, host.stopped);
        assertFalse(host.collecting);
    }

    @Test public void uploadAuthorizationAndEndedTripErrorsStopTracking() {
        for (int status : new int[] { 401, 403, 404, 409 }) {
            setup(); tracker.start(); host.uploadStatus = status;
            tracker.offer("fix", host.clock);
            assertEquals(status, host.stopped);
            assertFalse(tracker.isRunning());
        }
    }

    @Test public void stopCancelsQueuedAndLateCallbacks() {
        tracker.start(); host.autoUpload = false;
        tracker.offer("in-flight", host.clock);
        host.advance(1000); tracker.offer("queued", host.clock);
        SchoolHopLocationTracker.ResultCallback late = host.upload;
        tracker.stop(0);
        late.complete(200); host.advance(60000);
        assertEquals(List.of("in-flight"), host.uploads);
        assertFalse(host.collecting);
        assertTrue(host.jobs.isEmpty());
    }

    @Test public void previousSessionCallbacksCannotStopOrUploadIntoNewSession() {
        host.autoValidate = false; tracker.start();
        SchoolHopLocationTracker.ValidationCallback previous = host.validation;
        tracker.start();
        previous.complete(403, 0);
        assertTrue(tracker.isRunning());
        assertFalse(host.collecting);
        host.validation.complete(200, host.clock + 3600000);
        assertTrue(host.collecting);
        assertEquals(-1, host.stopped);
    }

    static final class Fake implements SchoolHopLocationTracker.Host {
        long clock = 100000, lifetime = 3600000;
        boolean permission = true, collecting, autoValidate = true, autoUpload = true;
        int validations, stopped = -1, validateStatus = 200, uploadStatus = 200;
        SchoolHopLocationTracker.ValidationCallback validation;
        SchoolHopLocationTracker.ResultCallback upload;
        final List<Object> uploads = new ArrayList<>();
        final PriorityQueue<Job> jobs = new PriorityQueue<>(Comparator.comparingLong(j -> j.when));
        public long now() { return clock; }
        public void schedule(Runnable task, long delay) { jobs.add(new Job(clock + delay, task)); }
        public void cancel(Runnable task) { jobs.removeIf(job -> job.task == task); }
        public boolean permitted() { return permission; }
        public void collect(boolean enabled) { collecting = enabled; }
        public void validate(SchoolHopLocationTracker.ValidationCallback callback) {
            validations++; validation = callback;
            if (autoValidate) callback.complete(validateStatus, clock + lifetime);
        }
        public void upload(Object fix, SchoolHopLocationTracker.ResultCallback callback) {
            uploads.add(fix); upload = callback;
            if (autoUpload) callback.complete(uploadStatus);
        }
        public void stopped(int status) { stopped = status; }
        public void diagnostic(SchoolHopLocationTracker.Event event, int status) { }
        void advance(long ms) {
            long until = clock + ms;
            while (!jobs.isEmpty() && jobs.peek().when <= until) {
                Job job = jobs.remove(); clock = job.when; job.task.run();
            }
            clock = until;
        }
        static final class Job {
            final long when; final Runnable task;
            Job(long when, Runnable task) { this.when = when; this.task = task; }
        }
    }
}
