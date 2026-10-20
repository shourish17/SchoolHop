package com.schoolhop.app;

/** Native, main-thread state machine. No WebView, coordinates, credentials or disk queue. */
final class SchoolHopLocationTracker {
    static final long POST_THROTTLE_MS = 4000;
    static final long FIX_MAX_AGE_MS = 30000;
    static final long VALIDATION_INTERVAL_MS = 30000;
    static final long AUTHORIZATION_LEASE_MS = 120000;
    static final long TICK_MS = 1000;

    enum Event { VALIDATING, VALIDATED, FIX, UPLOAD, UPLOADED, RETRY_WAIT, PAUSED, STOPPED }

    interface Host {
        long now();
        void schedule(Runnable task, long delayMs);
        void cancel(Runnable task);
        boolean permitted();
        void collect(boolean enabled);
        void validate(ValidationCallback callback);
        void upload(Object sample, ResultCallback callback);
        void stopped(int status);
        void diagnostic(Event event, int status);
    }

    interface ValidationCallback { void complete(int status, long expiresAt); }
    interface ResultCallback { void complete(int status); }

    private final Host host;
    private final Runnable pulse = this::tick;
    private volatile boolean running;
    private boolean collecting;
    private boolean busy;
    private boolean validated;
    private long generation;
    private long expiresAt;
    private long leaseUntil;
    private long validateAt;
    private long uploadAt;
    private long lastAttemptedFix = Long.MIN_VALUE;
    private long pendingFixAt;
    private Object pending;
    private int failures;

    SchoolHopLocationTracker(Host host) { this.host = host; }

    void start() {
        abandon();
        running = true;
        expiresAt = host.now() + AUTHORIZATION_LEASE_MS;
        validateAt = host.now();
        uploadAt = host.now();
        lastAttemptedFix = Long.MIN_VALUE;
        failures = 0;
        validated = false;
        tick();
    }

    boolean isRunning() { return running; }
    boolean isCollecting() { return collecting; }

    void offer(Object sample, long capturedAt) {
        long age = host.now() - capturedAt;
        if (!running || !collecting || age < 0 || age > FIX_MAX_AGE_MS || capturedAt <= lastAttemptedFix) return;
        if (pending != null && capturedAt <= pendingFixAt) return;
        pending = sample;
        pendingFixAt = capturedAt;
        host.diagnostic(Event.FIX, 0);
        tick();
    }

    void networkAvailable() {
        if (!running) return;
        // Revalidate before uploading after recovery. Do not bypass upload backoff.
        validateAt = host.now();
        tick();
    }

    void stop(int status) {
        if (!running) return;
        abandon();
        host.diagnostic(Event.STOPPED, status);
        host.stopped(status);
    }

    void abandon() {
        running = false;
        generation++;
        pending = null;
        busy = false;
        host.cancel(pulse);
        if (collecting) host.collect(false);
        collecting = false;
    }

    private void tick() {
        host.cancel(pulse);
        if (!running) return;
        long now = host.now();
        if (!host.permitted()) { stop(403); return; }
        if (now >= expiresAt) { stop(409); return; }
        if (collecting && now >= leaseUntil) {
            collecting = false;
            pending = null;
            host.collect(false);
            host.diagnostic(Event.PAUSED, 0);
        }
        host.schedule(pulse, TICK_MS);
        if (busy) return;
        if (now >= validateAt) {
            busy = true;
            long requestGeneration = generation;
            host.diagnostic(Event.VALIDATING, 0);
            host.validate((status, deadline) -> {
                if (!running || requestGeneration != generation) return;
                busy = false;
                if (terminal(status)) { stop(status); return; }
                validateAt = host.now() + VALIDATION_INTERVAL_MS;
                if (status == 200 && deadline > host.now()) {
                    expiresAt = deadline;
                    leaseUntil = host.now() + AUTHORIZATION_LEASE_MS;
                    validated = true;
                    if (!collecting) {
                        collecting = true;
                        host.collect(true);
                    }
                    if (running) host.diagnostic(Event.VALIDATED, 200);
                } else {
                    host.diagnostic(Event.RETRY_WAIT, status);
                }
                tick();
            });
            return;
        }
        if (!validated || !collecting || now >= leaseUntil || pending == null || now < uploadAt) return;
        if (now - pendingFixAt > FIX_MAX_AGE_MS) { pending = null; return; }
        Object sample = pending;
        lastAttemptedFix = pendingFixAt;
        pending = null;
        busy = true;
        long requestGeneration = generation;
        uploadAt = now + POST_THROTTLE_MS;
        host.diagnostic(Event.UPLOAD, 0);
        host.upload(sample, status -> {
            if (!running || requestGeneration != generation) return;
            busy = false;
            if (terminal(status)) { stop(status); return; }
            if (status >= 200 && status < 300) {
                failures = 0;
                host.diagnostic(Event.UPLOADED, status);
            } else {
                failures = Math.min(failures + 1, 4);
                uploadAt = Math.max(uploadAt, host.now() + Math.min(30000, 5000L << (failures - 1)));
                host.diagnostic(Event.RETRY_WAIT, status);
            }
            // Never replay an ambiguously accepted fix. Only a newer fix may be sent.
            tick();
        });
    }

    private static boolean terminal(int status) {
        return status == 400 || status == 401 || status == 403 || status == 404 || status == 409 || status == 422;
    }
}
