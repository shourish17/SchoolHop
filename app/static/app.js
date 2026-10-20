const state = {
  token: localStorage.getItem("schoolhop_token"),
  config: {},
  me: null,
  registration: { email: null, name: null, phone: null, token: null },
  pendingInviteToken: localStorage.getItem("schoolhop_pending_invite"),
  schools: [],
  groups: [],
  discoverGroups: [],
  children: [],
  group: null,
  groupChildren: [],
  trips: [],
  history: [],
  notifications: [],
  pendingActions: [],
  changeRequests: [],
  historyLoaded: false,
  historyLoading: false,
  selectedTripId: sessionStorage.getItem("schoolhop_selected_trip"),
  selectedTripFallbackNotified: null,
  selectedTripActionsRequestId: 0,
  selectedRosterDriverId: sessionStorage.getItem("schoolhop_selected_roster_driver"),
  rosterDriverWarningId: null,
  refreshGroupRequestId: 0,
  pendingTab: null,
  pendingSwapRequestId: null,
  activeManageSection: "invite",
  addSchoolTarget: "start",
  pendingJoinGroupId: localStorage.getItem("schoolhop_pending_join_group"),
  showTripHistory: false,
  watchId: null,
  trackingTripId: null,
  locationPollId: null,
  locationPollTripId: null,
  locationPollInFlight: false,
  routePollInFlight: false,
  lastLocationPostAt: 0,
  queuedLocationPost: null,
  locationPostTimer: null,
  locationPostInFlight: false,
  notificationSwipe: null,
  knownNotificationIds: new Set(),
  notificationIdsSeeded: false,
  nativePushListenerRegistered: false,
  nativePushSigningOut: false,
  nativeNotificationChannelsCreated: false,
  androidFirebaseMessagingConfigured: null,
};

const ROUTE_REFRESH_MS = 30000;
let notificationAudioContext = null;
let notificationAudioElement = null;
let nativePushQueue = Promise.resolve();
let nativePushRegistration = null;

const $ = (id) => document.getElementById(id);
const nativeConfig = window.SCHOOLHOP_NATIVE_CONFIG || {};
const nativeApiBaseUrl = nativeConfig.apiBaseUrl ? String(nativeConfig.apiBaseUrl).replace(/\/$/, "") : "";
const nativeAppVersion = nativeConfig.appVersion || "";
const isNativeApp = Boolean(window.Capacitor && typeof window.Capacitor.isNativePlatform === "function" && window.Capacitor.isNativePlatform());
const nativePlugins = () => (window.Capacitor && window.Capacitor.Plugins) || {};
const escapeHTML = (value) => String(value == null ? "" : value).replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
})[char]);

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 3600);
}

async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (state.token && !headers.Authorization) headers.Authorization = `Bearer ${state.token}`;
  const started = performance.now();
  const response = await fetch(apiUrl(path), { ...options, headers, cache: "no-store" });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  const duration = performance.now() - started;
  if (duration > 400 && path.startsWith("/api/")) {
    console.info("[api] slow request", { path, duration: Math.round(duration), serverTiming: response.headers.get("Server-Timing") });
  }
  if (!response.ok) {
    const detail = data && data.detail;
    const message = detail && typeof detail === "object"
      ? [detail.message, ...(detail.reasons || [])].filter(Boolean).join(" ")
      : detail;
    throw new Error(message || response.statusText);
  }
  return data;
}

function apiUrl(path) {
  if (!isNativeApp || !nativeApiBaseUrl || !path.startsWith("/")) return path;
  return `${nativeApiBaseUrl}${path}`;
}

function nativePlatform() {
  if (!isNativeApp || !window.Capacitor || typeof window.Capacitor.getPlatform !== "function") return "web";
  return window.Capacitor.getPlatform();
}

function reportClientError(error, context = {}) {
  fetch(apiUrl("/api/client-errors"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: error && error.message ? error.message : String(error || "Unknown client error"),
      source: context.source || null,
      stack: error && error.stack ? String(error.stack).slice(0, 4000) : null,
      user_agent: navigator.userAgent,
    }),
    cache: "no-store",
  }).catch(() => undefined);
}

function settledValue(result, fallback, source) {
  if (result.status === "fulfilled") return result.value;
  reportClientError(result.reason, { source });
  return fallback;
}

function formJSON(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  Object.keys(data).forEach((key) => {
    if (typeof data[key] === "string") data[key] = data[key].trim();
    if (data[key] === "") data[key] = null;
  });
  return data;
}

function normalizeGroupName(name) {
  return String(name || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function fillSelect(select, rows, label, value = "id") {
  if (!select) return;
  select.innerHTML = rows.length
    ? rows.map((row) => `<option value="${escapeHTML(row[value])}">${escapeHTML(label(row))}</option>`).join("")
    : `<option value="">None available</option>`;
}

function fillSelectWithPlaceholder(select, rows, label, placeholder, value = "id") {
  if (!select) return;
  select.innerHTML = `<option value="">${escapeHTML(placeholder)}</option>${
    rows.map((row) => `<option value="${escapeHTML(row[value])}">${escapeHTML(label(row))}</option>`).join("")
  }`;
}

function rememberSelectedTrip(tripId) {
  state.selectedTripId = tripId || null;
  if (state.selectedTripId) sessionStorage.setItem("schoolhop_selected_trip", state.selectedTripId);
  else sessionStorage.removeItem("schoolhop_selected_trip");
}

function rememberRosterDriver(driverId) {
  state.selectedRosterDriverId = driverId || null;
  if (state.selectedRosterDriverId) sessionStorage.setItem("schoolhop_selected_roster_driver", state.selectedRosterDriverId);
  else sessionStorage.removeItem("schoolhop_selected_roster_driver");
}

function selectedGroupId() {
  const groupSelect = $("groupSelect");
  return (groupSelect && groupSelect.value) || (state.groups[0] && state.groups[0].id) || "";
}

function selectedTrip() {
  const activeTrip = $("activeTrip");
  const activeTripId = activeTrip ? activeTrip.value : "";
  const trips = activeTrips();
  return trips.find((trip) => trip.id === activeTripId) || trips.find((trip) => trip.id === state.selectedTripId) || null;
}

function isDriver(trip) {
  return Boolean(trip && state.me && trip.driver_user_id === state.me.id);
}

function setTab(tabId) {
  document.querySelectorAll(".tabs button, .tab").forEach((el) => el.classList.remove("active"));
  const tabButton = document.querySelector(`.tabs button[data-tab="${tabId}"]`);
  const tab = $(tabId);
  if (tabButton) tabButton.classList.add("active");
  if (tab) tab.classList.add("active");
}

function statusTone(status) {
  if (status === "started") return "good";
  if (["cancelled", "driver_unavailable", "declined", "ignored"].includes(status)) return "bad";
  if (status === "delayed") return "warn";
  return "muted";
}

function pill(label, tone = "muted") {
  return `<span class="pill ${tone}">${escapeHTML(label)}</span>`;
}

function empty(message) {
  return `<div class="empty">${escapeHTML(message)}</div>`;
}

function todayKey() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function isArchivedTrip(trip) {
  return trip.service_date < todayKey() || ["completed", "cancelled", "driver_unavailable", "ignored"].includes(trip.status);
}

function activeTrips() {
  return state.trips.filter((trip) => !isArchivedTrip(trip));
}

function isTodayTrip(trip) {
  return Boolean(trip && trip.service_date === todayKey());
}

function isActionableTrip(trip) {
  return Boolean(trip && ["planned", "delayed", "started"].includes(trip.status) && trip.service_date >= todayKey());
}

function hasAcceptedTrip(trip) {
  return Boolean(trip && trip.my_response && trip.my_response.status === "accepted");
}

function tripResponseStatus(trip) {
  return trip && trip.my_response ? trip.my_response.status : "pending";
}

function isPendingTripAssignment(trip) {
  return Boolean(isDriver(trip) && tripResponseStatus(trip) === "pending");
}

function formatStatus(status) {
  return String(status || "pending").replace(/^\w/, (char) => char.toUpperCase());
}

function canDeleteRoster(trip) {
  if (!trip || trip.status !== "planned" || !state.me) return false;
  const groupCreatorId = state.group && state.group.group && state.group.group.created_by;
  return trip.created_by === state.me.id || groupCreatorId === state.me.id;
}

function canEndTrip(trip) {
  return Boolean(trip && trip.status === "started" && (isDriver(trip) || trip.can_admin_end_trip));
}

function tripStatusLabel(trip) {
  if (trip && trip.end_reason === "admin_ended") return "Ended by Admin";
  return trip ? trip.status : "";
}

function mapLocation(trip) {
  const location = trip && trip.latest_location;
  if (!location || location.latitude == null || location.longitude == null) return null;
  const latitude = Number(location.latitude);
  const longitude = Number(location.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  return { ...location, latitude, longitude };
}

function formatClock(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function formatDuration(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value)) return "";
  const minutes = Math.max(1, Math.round(value / 60));
  return `${minutes} min`;
}

function formatDistance(meters) {
  const value = Number(meters);
  if (!Number.isFinite(value) || value < 0) return "";
  if (value < 1000) return `${Math.round(value)} m away`;
  return `${(value / 1000).toFixed(1)} km away`;
}

function routeStatusMessage(reason, fallback) {
  if (reason === "location_missing") return "Waiting for driver's location...";
  if (reason === "location_stale") return "Waiting for updated driver location...";
  if (reason === "destination_missing") return "Destination unavailable";
  if (reason === "trip_not_active") return "Live ETA is available only during an active trip";
  return fallback || "ETA temporarily unavailable";
}

function lastLocationLabel(trip) {
  const location = trip && trip.latest_location;
  if (!location || !location.received_at) return "";
  return `Last location update: ${formatClock(location.received_at)}`;
}

function etaTitle(route) {
  if (!route) return "ETA";
  if (route.trip_type === "pickup") return "ETA to School";
  if (route.scope === "your_child" && route.stops && route.stops.length) {
    return `${route.stops[route.stops.length - 1].name} arriving`;
  }
  return "ETA to drop-off";
}

function etaSummaryHTML(trip) {
  if (!trip || trip.status !== "started") return "";
  const location = mapLocation(trip);
  const route = trip.latest_route;
  const lastLocation = lastLocationLabel(trip);
  if (route && route.eta_at && route.duration_seconds != null) {
    const duration = formatDuration(route.duration_seconds);
    const distance = formatDistance(route.distance_meters);
    return `
      <div class="eta-summary">
        <strong>${escapeHTML(etaTitle(route))}</strong>
        <span>${escapeHTML([formatClock(route.eta_at), duration].filter(Boolean).join(" · "))}</span>
        ${distance ? `<small>${escapeHTML(distance)}</small>` : ""}
        ${lastLocation ? `<small>${escapeHTML(lastLocation)}</small>` : ""}
      </div>`;
  }
  const message = routeStatusMessage(trip.route_reason, !location ? "Waiting for driver's location..." : trip.route_message);
  return `
    <div class="eta-summary muted">
      <strong>${escapeHTML(message)}</strong>
      ${lastLocation ? `<small>${escapeHTML(lastLocation)}</small>` : ""}
    </div>`;
}

async function init() {
  observeHeaderSize();
  state.config = await api("/api/config").catch(() => ({}));
  if (nativeAppVersion) state.config.app_version = nativeAppVersion;
  hydrateUrlActions();
  wireEvents();
  wireNativeEvents();
  registerServiceWorker();
  await refresh();
  await registerNativePushToken();
}

function observeHeaderSize() {
  const header = document.querySelector(".topbar");
  const update = () => document.documentElement.style.setProperty("--topbar-height", `${header.getBoundingClientRect().height}px`);
  update();
  new ResizeObserver(update).observe(header);
}

function registerServiceWorker() {
  if (!isNativeApp && "serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/service-worker.js").catch(() => undefined);
  }
}

function hydrateUrlActions() {
  const params = new URLSearchParams(window.location.search);
  const token = params.get("invite") || state.pendingInviteToken;
  const input = document.querySelector("#acceptInviteForm input[name='token']");
  if (token) {
    state.pendingInviteToken = token;
    localStorage.setItem("schoolhop_pending_invite", token);
    if (input) input.value = token;
  }
  const resetToken = params.get("reset");
  if (resetToken) {
    const resetInput = document.querySelector("#resetPasswordForm input[name='token']");
    if (resetInput) resetInput.value = resetToken;
    toggleAuth("resetPassword");
  }
  state.pendingTab = params.get("tab") || (token ? "group" : null);
  if (params.get("trip")) {
    rememberSelectedTrip(params.get("trip"));
  }
  state.pendingSwapRequestId = params.get("swap_request");
}

function wireEvents() {
  $("showLogin").addEventListener("click", () => toggleAuth("login"));
  $("showRegister").addEventListener("click", () => toggleAuth("registerStart"));
  $("showForgotPassword").addEventListener("click", () => toggleAuth("forgotPassword"));
  document.querySelectorAll(".back-login").forEach((button) => button.addEventListener("click", () => toggleAuth("login")));
  document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
  document.querySelectorAll("[data-manage-section]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeManageSection = button.dataset.manageSection;
      renderManagePanels();
    });
  });
  document.querySelectorAll(".add-school-toggle").forEach((button) => {
    button.addEventListener("click", () => showSchoolForm(button.dataset.schoolTarget || "start"));
  });

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    state.installPrompt = event;
    $("installApp").classList.remove("hidden");
  });
  $("installApp").addEventListener("click", async () => {
    if (!state.installPrompt) return;
    state.installPrompt.prompt();
    await state.installPrompt.userChoice.catch(() => undefined);
    state.installPrompt = null;
    $("installApp").classList.add("hidden");
  });

  submit("loginForm", async (form) => {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify(formJSON(form)) });
    setToken(data.token);
  });
  submit("forgotPasswordForm", async (form) => {
    const data = await api("/api/auth/password-reset/start", { method: "POST", body: JSON.stringify(formJSON(form)) });
    toast(data.message || "If an account exists for this email, a password reset link has been sent.");
    toggleAuth("login");
  }, false);
  submit("resetPasswordForm", async (form) => {
    const data = await api("/api/auth/password-reset/complete", { method: "POST", body: JSON.stringify(formJSON(form)) });
    window.history.replaceState({}, "", window.location.pathname);
    form.reset();
    toast(data.message || "Your password has been reset. You can sign in now.");
    toggleAuth("login");
  }, false);
  submit("registerStartForm", async (form) => {
    const payload = formJSON(form);
    const data = await api("/api/auth/register/start", { method: "POST", body: JSON.stringify(payload) });
    state.registration = { email: data.email, name: payload.name, phone: payload.phone, token: null };
    $("registerMessage").textContent = `We sent a verification code to ${data.masked_email}.`;
    if (data.verification_code) document.querySelector("#registerVerifyForm input[name='code']").value = data.verification_code;
    toggleAuth("registerVerify");
  }, false);
  submit("registerVerifyForm", async (form) => {
    const payload = { email: state.registration.email, code: formJSON(form).code };
    const data = await api("/api/auth/register/verify", { method: "POST", body: JSON.stringify(payload) });
    state.registration.token = data.registration_token;
    toggleAuth("registerComplete");
  }, false);
  submit("registerCompleteForm", async (form) => {
    const payload = {
      email: state.registration.email,
      registration_token: state.registration.token,
      password: formJSON(form).password,
    };
    const data = await api("/api/auth/register/complete", { method: "POST", body: JSON.stringify(payload) });
    setToken(data.token);
  });
  $("resendCode").addEventListener("click", async () => {
    if (!state.registration.email) return toast("Start account creation first");
    try {
      const data = await api("/api/auth/register/resend", {
        method: "POST",
        body: JSON.stringify({ email: state.registration.email, name: state.registration.name, phone: state.registration.phone }),
      });
      $("registerMessage").textContent = `We sent a new code to ${data.masked_email}.`;
      if (data.verification_code) document.querySelector("#registerVerifyForm input[name='code']").value = data.verification_code;
    } catch (error) {
      toast(error.message);
    }
  });

  submit("schoolForm", async (form) => {
    const data = formJSON(form);
    if (!data.country) data.country = "DE";
    const school = await api("/api/schools", { method: "POST", body: JSON.stringify(data) });
    await refreshSchools();
    selectSchoolForWorkflow(school && school.id);
    form.reset();
    form.elements.country.value = "DE";
    form.classList.add("hidden");
    return school;
  });
  submit("groupForm", async (form) => {
    const data = formJSON(form);
    const duplicate = state.discoverGroups.find((group) => group.school_id === data.school_id && normalizeGroupName(group.name) === normalizeGroupName(data.name));
    if (duplicate) throw new Error("A carpool with this name already exists for this school.");
    return api("/api/groups", { method: "POST", body: JSON.stringify(data) });
  });
  submit("acceptInviteForm", async (form) => {
    const token = formJSON(form).token;
    const result = await api(`/api/invitations/${encodeURIComponent(token)}/accept`, { method: "POST", body: "{}" });
    state.pendingActions = state.pendingActions.filter((action) => action.type !== "invitation" || action.action_url !== `/?invite=${token}`);
    clearPendingInvite(token);
    renderAll();
    return result;
  });
  $("declineInvite").addEventListener("click", declineInvitation);
  submit("requestAccessForm", async (form) => {
    const data = formJSON(form);
    delete data.school_id;
    try {
      const request = await api("/api/groups/access-requests", { method: "POST", body: JSON.stringify(data) });
      state.pendingJoinGroupId = request.group_id || data.group_id;
      localStorage.setItem("schoolhop_pending_join_group", state.pendingJoinGroupId);
      renderAccessRequestOptions();
      toast("Your request is pending approval.");
    } catch (error) {
      if (/Leave your current group/i.test(error.message)) {
        throw new Error("You have to leave your existing carpool first.");
      }
      throw error;
    }
  });
  $("requestSchool").addEventListener("change", renderAccessGroupOptions);
  $("settingsGroupSelect").addEventListener("change", () => {
    $("groupSelect").value = $("settingsGroupSelect").value;
    refreshGroupContext().catch((error) => toast(error.message));
  });
  submit("inviteForm", async (form) => {
    const invitation = await api(`/api/groups/${selectedGroupId()}/invitations`, { method: "POST", body: JSON.stringify(formJSON(form)) });
    await refreshGroupContext();
    toast("Invitation created");
    return invitation;
  }, false);
  submit("approvalForm", async (form) => {
    const data = formJSON(form);
    const childId = data.child_id;
    delete data.child_id;
    data.approved = form.approved.checked;
    return api(`/api/groups/${selectedGroupId()}/children/${childId}/driver-approvals`, { method: "POST", body: JSON.stringify(data) });
  });
  submit("childForm", saveChild);
  submit("tripForm", async (form) => {
    const data = formJSON(form);
    if (!data.driver_user_id) throw new Error("Choose a driver for this roster.");
    data.child_ids = [...$("tripChildren").querySelectorAll("input:checked")].map((input) => input.value);
    if (!data.child_ids.length) throw new Error("Choose at least one child");
    const submitter = form.querySelector("button[type='submit']");
    const trip = await withButtonLock(submitter, () => api(`/api/groups/${selectedGroupId()}/trips`, { method: "POST", body: JSON.stringify(data) }));
    if (trip && trip.driver_user_id && trip.driver_user_id !== data.driver_user_id) {
      throw new Error("Roster saved with a different driver than selected. Reload and verify before using this roster.");
    }
    if (trip && trip.id) rememberSelectedTrip(trip.id);
    rememberRosterDriver(data.driver_user_id);
    return trip;
  });

  $("groupSelect").addEventListener("change", () => refreshGroupContext().catch((error) => toast(error.message)));
  $("activeTrip").addEventListener("change", async () => {
    rememberSelectedTrip($("activeTrip").value);
    await refreshSelectedTripActions();
    renderAll();
  });
  $("tripDriver").addEventListener("change", () => {
    rememberRosterDriver($("tripDriver").value);
  });
  $("logout").addEventListener("click", async () => {
    $("logout").disabled = true;
    try {
      await unregisterNativePushToken();
    } catch {
      toast("Could not unregister notifications. Connect to the internet and try Sign Out again.");
      $("logout").disabled = false;
      return;
    }
    try {
      await stopTrackingForLogout();
    } catch {
      toast("Could not stop GPS sharing. Keep SchoolHop open and try Sign Out again.");
      $("logout").disabled = false;
      return;
    }
    localStorage.removeItem("schoolhop_token");
    sessionStorage.removeItem("schoolhop_selected_trip");
    sessionStorage.removeItem("schoolhop_selected_roster_driver");
    state.token = null;
    state.me = null;
    state.selectedTripId = null;
    state.selectedRosterDriverId = null;
    stopLocationPolling();
    state.knownNotificationIds = new Set();
    state.notificationIdsSeeded = false;
    state.pendingTab = null;
    state.pendingSwapRequestId = null;
    toggleAuth("login");
    await refresh();
    $("logout").disabled = false;
  });
  $("leaveGroup").addEventListener("click", leaveGroup);
  $("transferOwnership").addEventListener("click", transferOwnership);
  $("accountChildrenLink").addEventListener("click", () => setTab("children"));
  $("deleteAccount").addEventListener("click", deleteAccount);
  $("cancelChildEdit").addEventListener("click", resetChildForm);
  $("acceptTrip").addEventListener("click", (event) => tripAction("responses", { status: "accepted" }, event.currentTarget));
  $("declineTrip").addEventListener("click", (event) => tripAction("responses", { status: "declined" }, event.currentTarget));
  $("delayTrip").addEventListener("click", reportDelay);
  $("swapTrip").addEventListener("click", showSwapPanel);
  $("confirmSwap").addEventListener("click", requestSwap);
  $("cancelSwap").addEventListener("click", () => $("swapPanel").classList.add("hidden"));
  $("showTripHistory").addEventListener("click", () => {
    state.showTripHistory = true;
    renderTrips();
    loadTripHistory().catch((error) => toast(error.message));
  });
  $("hideTripHistory").addEventListener("click", () => {
    state.showTripHistory = false;
    renderTrips();
  });
  $("startTrip").addEventListener("click", startTrip);
  $("endTrip").addEventListener("click", endTrip);
  $("sendLocation").addEventListener("click", () => sendCurrentLocation(true));
  document.querySelectorAll(".refresh-home").forEach((button) => {
    button.addEventListener("click", () => refresh().catch((error) => toast(error.message)));
  });
  $("refreshRosters").addEventListener("click", () => refresh().catch((error) => toast(error.message)));
  $("clearReadNotifications").addEventListener("click", clearReadNotifications);
  $("handoverButtons").addEventListener("click", onHandoverClick);
  document.addEventListener("click", onDocumentClick);
  document.addEventListener("pointerdown", unlockNotificationSound, { once: true });
  document.addEventListener("keydown", unlockNotificationSound, { once: true });
  document.addEventListener("pointerdown", onNotificationPointerDown);
  document.addEventListener("pointerup", onNotificationPointerUp);
  document.addEventListener("pointercancel", resetNotificationSwipe);
  document.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && event.target instanceof Element && event.target.closest(".notification-action") && !event.target.closest("button, a, input, select, textarea")) {
      event.preventDefault();
      onDocumentClick(event);
    }
  });
}

function toggleAuth(mode) {
  $("showLogin").classList.toggle("active", mode === "login");
  $("showRegister").classList.toggle("active", mode !== "login");
  $("loginForm").classList.toggle("hidden", mode !== "login");
  $("forgotPasswordForm").classList.toggle("hidden", mode !== "forgotPassword");
  $("resetPasswordForm").classList.toggle("hidden", mode !== "resetPassword");
  $("registerStartForm").classList.toggle("hidden", mode !== "registerStart");
  $("registerVerifyForm").classList.toggle("hidden", mode !== "registerVerify");
  $("registerCompleteForm").classList.toggle("hidden", mode !== "registerComplete");
}

function setToken(token) {
  state.nativePushSigningOut = false;
  state.token = token;
  state.knownNotificationIds = new Set();
  state.notificationIdsSeeded = false;
  localStorage.setItem("schoolhop_token", token);
  registerNativePushToken().catch((error) => reportClientError(error, { source: "native-push-register" }));
}

function submit(formId, handler, doRefresh = true) {
  $(formId).addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await handler(event.currentTarget);
      if (doRefresh) {
        await refresh();
        toast("Saved");
      }
    } catch (error) {
      reportClientError(error, { source: formId });
      toast(error.message);
    }
  });
}

async function refresh() {
  if (!state.token) {
    $("auth").classList.remove("hidden");
    $("app").classList.add("hidden");
    $("logout").classList.add("hidden");
    $("sessionSummary").textContent = "Private school carpool";
    return;
  }
  $("auth").classList.add("hidden");
  $("app").classList.remove("hidden");
  $("logout").classList.remove("hidden");
  const [meResult, groups, schoolsResult, discoverGroupsResult, childrenResult] = await Promise.all([
    api("/api/me"),
    api("/api/groups"),
    api("/api/schools").catch((error) => {
      reportClientError(error, { source: "schools-refresh" });
      return [];
    }),
    api("/api/groups/discover").catch((error) => {
      reportClientError(error, { source: "discover-groups-refresh" });
      return [];
    }),
    api("/api/children").catch((error) => {
      reportClientError(error, { source: "children-refresh" });
      return [];
    }),
  ]);
  state.me = meResult.user;
  $("sessionSummary").textContent = nativeAppVersion ? `${state.me.email} · v${nativeAppVersion}` : state.me.email;
  state.schools = schoolsResult;
  state.groups = groups;
  if (state.pendingJoinGroupId && state.groups.some((group) => group.id === state.pendingJoinGroupId)) {
    state.pendingJoinGroupId = null;
    localStorage.removeItem("schoolhop_pending_join_group");
  }
  state.discoverGroups = discoverGroupsResult;
  state.children = childrenResult;
  fillSelect($("schoolSelect"), state.schools, (s) => `${s.name}${s.city ? `, ${s.city}` : ""}`);
  fillSelect($("childSchoolSelect"), state.schools, (s) => `${s.name}${s.city ? `, ${s.city}` : ""}`);
  fillSelect($("groupSelect"), state.groups, (g) => `${g.name} - ${g.school_name}`);
  fillSelect($("settingsGroupSelect"), state.groups, (g) => `${g.name} - ${g.school_name}`);
  renderAccessRequestOptions();
  if (state.groups[0] && !$("groupSelect").value) $("groupSelect").value = state.groups[0].id;
  if ($("settingsGroupSelect") && $("groupSelect")) $("settingsGroupSelect").value = $("groupSelect").value;
  await refreshGroupContext();
}

async function refreshSchools() {
  state.schools = await api("/api/schools").catch((error) => {
    reportClientError(error, { source: "schools-refresh" });
    return state.schools;
  });
  fillSelect($("schoolSelect"), state.schools, (s) => `${s.name}${s.city ? `, ${s.city}` : ""}`);
  fillSelect($("childSchoolSelect"), state.schools, (s) => `${s.name}${s.city ? `, ${s.city}` : ""}`);
}

function showSchoolForm(target) {
  state.addSchoolTarget = target || "start";
  $("schoolForm").classList.remove("hidden");
  $("schoolForm").scrollIntoView({ behavior: "smooth", block: "start" });
}

function selectSchoolForWorkflow(schoolId) {
  if (!schoolId) return;
  const select = state.addSchoolTarget === "join" ? $("requestSchool") : $("schoolSelect");
  if (select && [...select.options].some((option) => option.value === schoolId)) {
    select.value = schoolId;
    if (select.id === "requestSchool") renderAccessGroupOptions();
  }
}

async function refreshGroupContext() {
  const requestId = ++state.refreshGroupRequestId;
  const groupId = selectedGroupId();
  if (!groupId) {
    state.group = null;
    state.groupChildren = [];
    state.trips = [];
    stopTrackingIfNoLongerAllowed();
    state.history = [];
    state.historyLoaded = false;
    state.historyLoading = false;
    const [notificationsResult, pendingActionsResult] = state.token
      ? await Promise.allSettled([api("/api/notifications"), api("/api/pending-actions")])
      : [[], []];
    const notifications = state.token ? settledValue(notificationsResult, state.notifications, "notifications-refresh") : [];
    const pendingActions = state.token ? settledValue(pendingActionsResult, state.pendingActions, "pending-actions-refresh") : [];
    if (requestId !== state.refreshGroupRequestId) return;
    setNotifications(notifications);
    state.pendingActions = pendingActions;
    renderAll();
    applyPendingNavigation();
    return;
  }
  state.history = [];
  state.historyLoaded = false;
  state.historyLoading = false;
  const [group, trips, groupChildrenResult, notificationsResult, pendingActionsResult] = await Promise.all([
    api(`/api/groups/${groupId}`),
    api(`/api/groups/${groupId}/trips`),
    api(`/api/groups/${groupId}/children`).catch((error) => {
      reportClientError(error, { source: "group-children-refresh" });
      return [];
    }),
    api("/api/notifications").then(
      (notifications) => ({ status: "fulfilled", value: notifications }),
      (error) => ({ status: "rejected", reason: error }),
    ),
    api("/api/pending-actions").then(
      (pendingActions) => ({ status: "fulfilled", value: pendingActions }),
      (error) => ({ status: "rejected", reason: error }),
    ),
  ]);
  if (requestId !== state.refreshGroupRequestId) return;
  state.group = group;
  state.groupChildren = groupChildrenResult;
  state.trips = trips;
  stopTrackingIfNoLongerAllowed();
  const notifications = settledValue(notificationsResult, state.notifications, "notifications-refresh");
  const pendingActions = settledValue(pendingActionsResult, state.pendingActions, "pending-actions-refresh");
  setNotifications(notifications);
  state.pendingActions = pendingActions;
  chooseSelectedTrip();
  await refreshSelectedTripActions();
  renderAll();
  applyPendingNavigation();
}

function chooseSelectedTrip() {
  const trips = activeTrips();
  if (state.selectedTripId && trips.some((trip) => trip.id === state.selectedTripId)) return;
  const unavailableTripId = state.selectedTripId;
  if (trips[0]) {
    rememberSelectedTrip(trips[0].id);
    if (unavailableTripId && state.selectedTripFallbackNotified !== unavailableTripId) {
      state.selectedTripFallbackNotified = unavailableTripId;
      toast("The selected trip is no longer available. Showing the next available trip.");
    }
  } else {
    rememberSelectedTrip(null);
  }
}

function stopTrackingIfNoLongerAllowed() {
  if (!state.trackingTripId) return;
  const trackedTrip = state.trips.find((trip) => trip.id === state.trackingTripId);
  if (trackedTrip && trackedTrip.status === "started" && isDriver(trackedTrip)) return;
  stopTracking();
}

async function loadTripHistory() {
  const groupId = selectedGroupId();
  if (!groupId || state.historyLoaded || state.historyLoading) return;
  state.historyLoading = true;
  renderTrips();
  try {
    state.history = await api(`/api/groups/${groupId}/trips/history`);
    state.historyLoaded = true;
  } finally {
    state.historyLoading = false;
    renderTrips();
  }
}

async function refreshSelectedTripActions() {
  const requestId = ++state.selectedTripActionsRequestId;
  const tripId = state.selectedTripId;
  state.changeRequests = [];
  if (!tripId) return;
  try {
    const requests = await api(`/api/trips/${tripId}/change-requests`);
    if (requestId === state.selectedTripActionsRequestId && state.selectedTripId === tripId) state.changeRequests = requests;
  } catch {
    if (requestId === state.selectedTripActionsRequestId && state.selectedTripId === tripId) state.changeRequests = [];
  }
}

function swapRequestById(requestId) {
  return state.changeRequests.find((request) => request.id === requestId) || null;
}

function applyPendingNavigation() {
  if (!state.pendingTab) return;
  if (state.pendingTab === "trip" && state.selectedTripId && $("activeTrip")) $("activeTrip").value = state.selectedTripId;
  setTab(state.pendingTab);
  state.pendingTab = null;
}

function renderAll() {
  renderHome();
  renderTrips();
  renderChildren();
  renderMore();
  renderAccount();
  renderNotifications();
}

function renderHome() {
  const trips = activeTrips();
  const startedTrips = trips.filter((trip) => trip.status === "started");
  const currentTrip = startedTrips[0] || null;
  const upcomingTrips = trips.filter((trip) => trip.status !== "started");
  const nextTrip = upcomingTrips[0] || null;
  const remainingTrips = upcomingTrips.slice(1);
  const trackedTrips = startedTrips.filter((trip) => mapLocation(trip));
  $("homeGroup").textContent = (state.group && state.group.group && state.group.group.name) || "None";
  $("homeTrips").textContent = String(trips.length);
  $("homeTracking").textContent = state.watchId !== null || trackedTrips.length ? "Active" : "Off";
  $("pendingActionPanel").classList.add("hidden");
  $("pendingActionList").innerHTML = "";
  $("homeCurrentTripPanel").classList.toggle("hidden", !currentTrip);
  $("homeCurrentTripList").innerHTML = currentTrip ? tripCard(currentTrip) : "";
  $("homeNextTripPanel").classList.toggle("hidden", !nextTrip);
  $("homeTripList").innerHTML = nextTrip ? tripCard(nextTrip) : "";
  $("homeUpcomingPanel").classList.toggle("hidden", !remainingTrips.length);
  $("homeUpcomingTripList").innerHTML = remainingTrips.map(tripCard).join("");
}

function actionCard(action) {
  const url = action.action_url || action.url || "";
  return `
    <article class="item-card notification-action" data-url="${escapeHTML(url)}" tabindex="0" role="button">
      <div class="item-head"><strong>${escapeHTML(action.title)}</strong></div>
      <p>${escapeHTML(action.body)}</p>
    </article>`;
}

function renderAccessRequestOptions() {
  const schoolsById = new Map();
  for (const group of state.discoverGroups) {
    schoolsById.set(group.school_id, {
      id: group.school_id,
      name: group.school_name,
      city: group.school_city,
    });
  }
  const schools = [...schoolsById.values()].sort((a, b) => `${a.name} ${a.city || ""}`.localeCompare(`${b.name} ${b.city || ""}`));
  fillSelect($("requestSchool"), schools, (school) => `${school.name}${school.city ? `, ${school.city}` : ""}`);
  if (state.pendingJoinGroupId) {
    const pendingGroup = state.discoverGroups.find((group) => group.id === state.pendingJoinGroupId);
    if (pendingGroup && $("requestSchool")) $("requestSchool").value = pendingGroup.school_id;
  }
  renderAccessGroupOptions();
}

function renderAccessGroupOptions() {
  const schoolId = $("requestSchool").value;
  const groups = state.discoverGroups.filter((group) => group.school_id === schoolId);
  fillSelect($("requestGroup"), groups, (group) => group.name);
  if (state.pendingJoinGroupId && groups.some((group) => group.id === state.pendingJoinGroupId)) $("requestGroup").value = state.pendingJoinGroupId;
  const pending = Boolean(state.pendingJoinGroupId && $("requestGroup").value === state.pendingJoinGroupId);
  $("pendingJoinStatus").classList.toggle("hidden", !pending);
  $("requestAccessForm").classList.toggle("hidden", pending);
}

function renderTrips() {
  const trips = activeTrips();
  const rosterDrivers = (state.group && state.group.members) || [];
  const selectedRosterDriverId = state.selectedRosterDriverId || $("tripDriver").value || "";
  fillSelectWithPlaceholder($("tripDriver"), rosterDrivers, (m) => `${m.name} (${m.email})`, "Choose driver");
  if (selectedRosterDriverId && rosterDrivers.some((driver) => driver.id === selectedRosterDriverId)) {
    $("tripDriver").value = selectedRosterDriverId;
    rememberRosterDriver(selectedRosterDriverId);
  } else {
    $("tripDriver").value = "";
    if (selectedRosterDriverId && state.rosterDriverWarningId !== selectedRosterDriverId) {
      state.rosterDriverWarningId = selectedRosterDriverId;
      toast("The selected driver is no longer available. Choose a replacement before saving.");
    }
  }
  $("tripChildren").innerHTML = state.groupChildren.length ? state.groupChildren.map((child) => `
    <label><input type="checkbox" value="${escapeHTML(child.id)}" /> ${escapeHTML(child.name)} <span>${escapeHTML(child.parent_name)}</span></label>
  `).join("") : `<p>No carpool children available.</p>`;
  $("tripList").innerHTML = trips.length ? trips.map(tripCard).join("") : empty("No active rosters or trips.");
  $("tripHistoryPanel").classList.toggle("hidden", !state.showTripHistory);
  $("historyTripList").innerHTML = state.historyLoading
    ? empty("Loading trip history...")
    : (state.history.length ? state.history.map((trip) => tripCard(trip, false)).join("") : empty("No historical trips yet."));
  fillSelect($("activeTrip"), trips, (t) => `${t.service_date} ${t.expected_time} ${t.trip_type} - ${(t.driver && t.driver.name) || "driver"}`);
  if (state.selectedTripId && trips.some((trip) => trip.id === state.selectedTripId)) {
    $("activeTrip").value = state.selectedTripId;
  } else {
    $("activeTrip").value = "";
  }
  renderTripConsole();
}

function tripCard(trip, selectable = true) {
  const driverLabel = (trip.driver && trip.driver.name) || "Assigned driver";
  const children = trip.children.map((child) => `${child.name}: pickup ${child.pickup_status}, drop-off ${child.dropoff_status}`).join("; ");
  const response = trip.my_response ? pill(trip.my_response.status, statusTone(trip.my_response.status)) : "";
  const selected = selectedTrip();
  const active = selectable && selected && selected.id === trip.id ? " selected" : "";
  const tracking = trip.status === "started" && mapLocation(trip);
  return `
    <article class="item-card trip-card${active}" data-trip="${escapeHTML(trip.id)}" data-tracking="${tracking ? "on" : "off"}">
      <div class="item-head">
        <div>
          <strong>${escapeHTML(trip.service_date)} · ${escapeHTML(trip.expected_time)}</strong>
          <span>${escapeHTML(trip.trip_type)} by ${escapeHTML(driverLabel)}</span>
        </div>
        <div>${pill(tripStatusLabel(trip), statusTone(trip.status))}${tracking ? pill("tracking on", "good") : ""}${response}</div>
      </div>
      <p>${escapeHTML(children || "No children visible.")}</p>
      ${etaSummaryHTML(trip)}
      ${selectable ? `
        <div class="mini-actions">
          <button class="ghost choose-trip" data-trip="${escapeHTML(trip.id)}" type="button">${tracking ? "View map" : "Open"}</button>
          ${canDeleteRoster(trip) ? `<button class="ghost danger delete-roster" data-trip="${escapeHTML(trip.id)}" type="button">Delete Roster</button>` : ""}
        </div>` : ""}
    </article>`;
}

function renderTripConsole() {
  const trip = selectedTrip();
  const location = mapLocation(trip);
  const trackingActive = state.watchId !== null || (trip && trip.status === "started" && location);
  $("trackingBanner").className = `tracking ${trackingActive ? "on" : "off"}`;
  $("trackingBanner").textContent = trackingMessage(trip, location);
  if (!trip) {
    $("tripResponseStatus").classList.add("hidden");
    $("acceptTrip").disabled = true;
    $("declineTrip").disabled = true;
    $("delayTrip").disabled = true;
    $("swapTrip").disabled = true;
    $("delayTrip").classList.add("hidden");
    $("swapTrip").classList.add("hidden");
    $("startTrip").disabled = true;
    $("endTrip").disabled = true;
    $("endTrip").classList.add("hidden");
    $("sendLocation").disabled = true;
    $("swapPanel").classList.add("hidden");
    $("swapRequestPanel").classList.add("hidden");
    $("swapRequestPanel").innerHTML = "";
    $("handoverButtons").innerHTML = empty("Select or create a trip.");
    $("tripMap").innerHTML = empty("Select an active trip to see its map.");
    stopLocationPolling();
    return;
  }
  const driver = isDriver(trip);
  const pendingAssignment = isPendingTripAssignment(trip);
  const actionable = driver && isActionableTrip(trip);
  const canStartToday = isTodayTrip(trip);
  const accepted = hasAcceptedTrip(trip);
  const responseStatus = tripResponseStatus(trip);
  $("tripResponseStatus").textContent = formatStatus(responseStatus);
  $("tripResponseStatus").className = `pill ${statusTone(responseStatus)}${driver || trip.my_response ? "" : " hidden"}`;
  $("acceptTrip").disabled = !pendingAssignment;
  $("declineTrip").disabled = !pendingAssignment;
  $("acceptTrip").classList.toggle("hidden", !pendingAssignment);
  $("declineTrip").classList.toggle("hidden", !pendingAssignment);
  $("delayTrip").disabled = !actionable;
  $("swapTrip").disabled = !actionable;
  $("delayTrip").classList.toggle("hidden", !actionable);
  $("swapTrip").classList.toggle("hidden", !actionable);
  $("startTrip").textContent = trip.status === "started" ? "Start GPS" : "Start trip";
  $("startTrip").disabled = !driver || !accepted || !["planned", "delayed", "started"].includes(trip.status) || !canStartToday;
  $("startTrip").title = !accepted
    ? "Accept this trip before starting it"
    : (!canStartToday ? "Trips can only be started on their service date" : "");
  $("endTrip").disabled = !canEndTrip(trip);
  $("endTrip").classList.toggle("hidden", !canEndTrip(trip));
  $("sendLocation").disabled = !driver || trip.status !== "started";
  reconcileLocationPolling(trip);
  renderTripMap(trip);
  renderSwapRequestPanel(trip);
  $("handoverButtons").innerHTML = handoverButtonsHTML(trip, driver);
}

function handoverButtonsHTML(trip, driver) {
  const type = trip.trip_type === "pickup" ? "picked_up" : "dropped_off";
  const label = type === "picked_up" ? "Picked Up" : "Dropped Off";
  return trip.children.map((child) => {
    const status = trip.trip_type === "pickup" ? child.pickup_status : child.dropoff_status;
    const disabled = !driver || status !== "pending" ? "disabled" : "";
    return `<article class="handover-card">
      <div><strong>${escapeHTML(child.name)}</strong>
        <span>Pickup ${escapeHTML(child.pickup_status)} · Drop-off ${escapeHTML(child.dropoff_status)}</span></div>
      <div class="mini-actions">
        <button data-child="${escapeHTML(child.child_id)}" data-type="${type}" ${disabled} type="button">${label}</button>
        <button class="ghost danger" data-child="${escapeHTML(child.child_id)}" data-type="absent" ${disabled} type="button">Absent</button>
      </div></article>`;
  }).join("");
}

function renderSwapRequestPanel(trip) {
  const panel = $("swapRequestPanel");
  const requests = state.changeRequests.filter((request) => request.trip_id === trip.id && request.request_type === "swap");
  const pendingRequest = requests.find((request) => request.status === "open" && state.me && request.proposed_driver_user_id === state.me.id);
  const finalRequest = (state.pendingSwapRequestId && requests.find((request) => request.id === state.pendingSwapRequestId))
    || requests.find((request) => request.status !== "open" && state.me && request.proposed_driver_user_id === state.me.id);
  const visibleRequest = pendingRequest || finalRequest;
  if (!visibleRequest) {
    panel.classList.add("hidden");
    panel.innerHTML = "";
    return;
  }
  panel.classList.remove("hidden");
  const finalStatus = visibleRequest.status !== "open" ? visibleRequest.status : "";
  panel.innerHTML = `
    <div>
      <strong>Swap request</strong>
      <p>${escapeHTML(visibleRequest.requester_name || "The assigned driver")} asked you to cover this trip.</p>
    </div>
    ${finalStatus ? `<div>${pill(formatStatus(finalStatus), statusTone(finalStatus))}</div>` : `
      <div class="mini-actions">
        <button class="decide-swap" data-request="${escapeHTML(visibleRequest.id)}" data-status="accepted" type="button">Accept</button>
        <button class="ghost danger decide-swap" data-request="${escapeHTML(visibleRequest.id)}" data-status="declined" type="button">Decline</button>
      </div>`}`;
}

function trackingMessage(trip, location) {
  if (state.watchId !== null) return "Tracking active. Your browser is sending driver GPS for this trip.";
  if (!trip) return "Tracking off. Select an active trip to see live GPS.";
  if (trip.status === "started" && location) {
    return location.message || (location.fresh ? "Live driver GPS is active." : "Driver GPS was received, but it may be stale.");
  }
  if (trip.status === "started") return "Waiting for the driver's first GPS signal.";
  return "Tracking off. The assigned driver must accept and start this trip first.";
}

function renderTripMap(trip) {
  if (!trip) {
    $("tripMap").innerHTML = empty("Select an active trip to see its map.");
    return;
  }
  const location = mapLocation(trip);
  if (!location) {
    $("tripMap").innerHTML = `
      <div class="map map-empty">
        <div>
          <strong>Map waiting for GPS</strong>
          <p>${trip.status === "started" ? "The map appears after the driver's browser sends the first location." : "Start the trip on its service date to enable live location."}</p>
        </div>
      </div>`;
    return;
  }
  const delta = 0.01;
  const left = location.longitude - delta;
  const right = location.longitude + delta;
  const top = location.latitude + delta;
  const bottom = location.latitude - delta;
  $("tripMap").innerHTML = `
    <div class="map-wrap">
      <iframe
        class="map"
        title="Driver map"
        loading="lazy"
        referrerpolicy="no-referrer-when-downgrade"
        src="https://www.openstreetmap.org/export/embed.html?bbox=${left}%2C${bottom}%2C${right}%2C${top}&layer=mapnik&marker=${location.latitude}%2C${location.longitude}">
      </iframe>
      <div class="vehicle-marker" aria-label="Driver GPS point" title="Driver GPS point">CAR</div>
    </div>
    <div class="route-summary">
      ${etaSummaryHTML(trip) || escapeHTML(location.message || (location.fresh ? "GPS fresh" : "GPS location received"))}
    </div>`;
}

function renderChildren() {
  fillSelect($("approvalChild"), state.children, (c) => `${c.name} (${c.year_group})`);
  fillSelect($("approvalDriver"), (state.group && state.group.members) || [], (m) => `${m.name} (${m.email})`);
  $("childrenList").innerHTML = state.children.length ? state.children.map((child) => `
    <article class="item-card">
      <div>
        <strong>${escapeHTML(child.name)}</strong>
        <span>${escapeHTML(child.year_group)} · ${escapeHTML(child.school_name)}</span>
      </div>
      <p>${escapeHTML(formatHomeAddress(child) || "No home pickup/drop-off address.")}</p>
      <div class="mini-actions">
        <button class="ghost edit-child" data-child="${escapeHTML(child.id)}" type="button">Edit</button>
        <button class="ghost danger remove-child" data-child="${escapeHTML(child.id)}" type="button">Remove</button>
      </div>
    </article>`).join("") : empty("Add a child profile to plan trips.");
}

function renderMore() {
  $("carpoolActiveView").classList.toggle("hidden", !state.group);
  $("carpoolEmptyView").classList.toggle("hidden", Boolean(state.group));
  $("schoolForm").classList.add("hidden");
  if (!state.group) {
    renderAccessRequestOptions();
    return;
  }
  const group = state.group.group;
  $("carpoolSummaryText").textContent = `${group.school_name}${group.school_city ? `, ${group.school_city}` : ""}`;
  $("carpoolSummary").innerHTML = `
    <article class="metric">
      <span>Carpool name</span>
      <strong>${escapeHTML(group.name)}</strong>
    </article>
    <article class="metric">
      <span>School</span>
      <strong>${escapeHTML(group.school_name)}</strong>
    </article>
    <article class="metric">
      <span>Families</span>
      <strong>${escapeHTML(state.group.members.length)}</strong>
    </article>`;
  renderJoinRequestCards($("carpoolJoinRequests"), { emptyWhenNone: false });
  $("leaveGroup").disabled = false;
  if ($("groupSelect")) $("groupSelect").value = selectedGroupId();
  if ($("settingsGroupSelect")) $("settingsGroupSelect").value = selectedGroupId();
  renderManagePanels();
}

function renderManagePanels() {
  const sections = ["invite", "families", "drivers", "settings"];
  sections.forEach((section) => {
    document.querySelectorAll(`[data-manage-section="${section}"]`).forEach((button) => button.classList.toggle("active", state.activeManageSection === section));
  });
  $("manageInvite").classList.toggle("hidden", state.activeManageSection !== "invite");
  $("manageFamilies").classList.toggle("hidden", state.activeManageSection !== "families");
  $("manageDrivers").classList.toggle("hidden", state.activeManageSection !== "drivers");
  $("manageSettings").classList.toggle("hidden", state.activeManageSection !== "settings");
  if (!state.group) return;

  const owner = isGroupOwner();
  const members = state.group.members.map((m) => `
    <article class="small-card">
      <div class="item-head">
        <div>
          <strong>${escapeHTML(m.name)}</strong>
          <span>${escapeHTML(m.email)} · ${escapeHTML(m.role)} · active</span>
        </div>
        ${m.id !== state.me.id && owner ? `<button class="ghost danger compact-action remove-member" data-user="${escapeHTML(m.id)}" type="button">Remove</button>` : ""}
      </div>
    </article>`).join("");
  $("familiesList").innerHTML = members || empty("No families.");

  const transferMembers = state.group.members.filter((member) => member.id !== state.me.id);
  fillSelect($("transferOwnerSelect"), transferMembers, (member) => `${member.name} (${member.email})`);
  $("ownerTransferPanel").classList.toggle("hidden", !owner || !transferMembers.length);
}

function isGroupOwner() {
  return Boolean(state.group && state.me && state.group.group.created_by === state.me.id);
}

function renderAccount() {
  const accountEmail = $("accountEmail");
  if (accountEmail) accountEmail.textContent = state.me ? state.me.email : "";
}

function renderNotifications() {
  const pendingCards = pendingAlertCards();
  const sentInvitationCards = sentInvitationAlertCards();
  const historyCards = state.notifications.length ? state.notifications.slice(0, 30).map((n) => `
    <article class="item-card notification-card" data-notification="${escapeHTML(n.id)}">
      <div class="notification-main${notificationActionUrl(n) ? " notification-action" : ""}" ${notificationActionUrl(n) ? `data-url="${escapeHTML(notificationActionUrl(n))}" tabindex="0" role="button"` : ""}>
        <div class="item-head"><strong>${escapeHTML(n.title)}</strong>${pill(n.kind)}</div>
        <p>${escapeHTML(n.body)}</p>
        <span>${escapeHTML(new Date(n.created_at).toLocaleString())}</span>
      </div>
      <button class="ghost danger notification-delete" data-notification="${escapeHTML(n.id)}" type="button" aria-label="Delete notification">Delete</button>
    </article>`).join("") : "";
  $("notificationList").innerHTML = [pendingCards, sentInvitationCards, historyCards].filter(Boolean).join("") || empty("No alerts yet.");
  $("clearReadNotifications").classList.toggle("hidden", !state.notifications.some((notification) => notification.read_at));
}

function pendingAlertCards() {
  const actions = state.pendingActions.map((action) => {
    if (action.type === "invitation") {
      const token = new URL(action.action_url || "", window.location.origin).searchParams.get("invite") || "";
      return `
        <article class="item-card">
          <div class="item-head"><strong>${escapeHTML(action.title)}</strong>${pill("pending")}</div>
          <p>${escapeHTML(action.body)}</p>
          <div class="mini-actions">
            <button class="accept-pending-invite" data-token="${escapeHTML(token)}" type="button">Accept</button>
            <button class="ghost danger decline-pending-invite" data-token="${escapeHTML(token)}" type="button">Decline</button>
          </div>
        </article>`;
    }
    if (action.type === "driver_assignment") {
      return `
        <article class="item-card">
          <div class="item-head"><strong>${escapeHTML(action.title)}</strong>${pill("pending")}</div>
          <p>${escapeHTML(action.body)}</p>
          <div class="mini-actions">
            <button class="accept-driver-assignment" data-trip="${escapeHTML(action.trip_id)}" type="button">Accept</button>
            <button class="ghost danger decline-driver-assignment" data-trip="${escapeHTML(action.trip_id)}" type="button">Decline</button>
          </div>
        </article>`;
    }
    if (action.type === "swap_request") {
      return `
        <article class="item-card">
          <div class="item-head"><strong>${escapeHTML(action.title)}</strong>${pill("pending")}</div>
          <p>${escapeHTML(action.body)}</p>
          <div class="mini-actions">
            <button class="decide-swap" data-request="${escapeHTML(action.id)}" data-status="accepted" type="button">Accept</button>
            <button class="ghost danger decide-swap" data-request="${escapeHTML(action.id)}" data-status="declined" type="button">Decline</button>
          </div>
        </article>`;
    }
    return actionCard(action);
  });
  const joinRequests = joinRequestCards();
  return [...actions, ...joinRequests].join("");
}

function joinRequestCards() {
  if (!state.group || !isGroupOwner()) return [];
  return (state.group.join_requests || []).map((request) => `
    <article class="item-card">
      <div class="item-head"><strong>Join Request</strong>${pill("pending")}</div>
      <p>${escapeHTML(request.requester_name)} (${escapeHTML(request.requester_email)}) requested to join ${escapeHTML(state.group.group.name)}.</p>
      ${request.requester_note ? `<p>${escapeHTML(request.requester_note)}</p>` : ""}
      <div class="mini-actions">
        <button class="approve-access" data-request="${escapeHTML(request.id)}" type="button">Approve</button>
        <button class="ghost danger decline-access" data-request="${escapeHTML(request.id)}" type="button">Decline</button>
      </div>
    </article>`);
}

function renderJoinRequestCards(container, { emptyWhenNone = true } = {}) {
  if (!container) return;
  const cards = joinRequestCards();
  container.classList.toggle("hidden", !cards.length && !emptyWhenNone);
  container.innerHTML = cards.length ? cards.join("") : (emptyWhenNone ? empty("No pending join requests.") : "");
}

function sentInvitationAlertCards() {
  if (!state.group || !isGroupOwner() || !state.group.invitations.length) return "";
  const invitations = state.group.invitations.slice(0, 10).map((inv) => {
    const inviteLink = inv.status === "pending" && inv.token ? `${state.config.public_site_url || window.location.origin}/?invite=${inv.token}` : "";
    return `
      <article class="item-card">
        <div class="item-head"><strong>Sent Invitation</strong>${pill(inv.status || "sent")}</div>
        <p>${escapeHTML(inv.email)}</p>
        ${inviteLink ? `<div class="mini-actions"><button class="ghost compact-action copy-invite" data-link="${escapeHTML(inviteLink)}" type="button">Copy Invite Link</button></div>` : ""}
      </article>`;
  }).join("");
  return invitations;
}

function notificationActionUrl(notification) {
  if (notification.action_url) return notification.action_url;
  if (notification.entity_type === "trip" && notification.entity_id) return `/?tab=trip&trip=${encodeURIComponent(notification.entity_id)}`;
  if (notification.entity_type === "group") return "/?tab=group";
  return "";
}

function setNotifications(notifications, { playSound = true } = {}) {
  const newNotification = notifications.find((notification) => notification.id && !state.knownNotificationIds.has(notification.id));
  state.notifications = notifications;
  notifications.forEach((notification) => {
    if (notification.id) state.knownNotificationIds.add(notification.id);
  });
  if (!state.notificationIdsSeeded) {
    state.notificationIdsSeeded = true;
    return;
  }
  if (playSound && newNotification) playNotificationSound(newNotification.id);
}

async function unlockNotificationSound() {
  if (!("AudioContext" in window) && !("webkitAudioContext" in window) && !("Audio" in window)) return false;
  try {
    if ("AudioContext" in window || "webkitAudioContext" in window) {
      const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
      notificationAudioContext ||= new AudioContextCtor();
      await notificationAudioContext.resume();
    }
    localStorage.setItem("schoolhopNotificationSoundEnabled", "1");
    return true;
  } catch (error) {
    console.warn("[notification-audio] Notification sound unlock failed", error);
    return false;
  }
}

function notificationToneUrl() {
  const sampleRate = 22050;
  const duration = 0.52;
  const samples = Math.floor(sampleRate * duration);
  const dataSize = samples * 2;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeString = (offset, value) => {
    for (let index = 0; index < value.length; index += 1) view.setUint8(offset + index, value.charCodeAt(index));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, dataSize, true);
  for (let index = 0; index < samples; index += 1) {
    const time = index / sampleRate;
    const frequency = time < 0.24 ? 880 : 660;
    const envelope = Math.min(1, index / 500) * Math.min(1, (samples - index) / 1800);
    view.setInt16(44 + index * 2, Math.sin(2 * Math.PI * frequency * time) * 0.32 * envelope * 32767, true);
  }
  const bytes = new Uint8Array(buffer);
  let binary = "";
  bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
  return `data:audio/wav;base64,${btoa(binary)}`;
}

async function playNotificationSound(notificationId = "") {
  if (localStorage.getItem("schoolhopNotificationSoundEnabled") !== "1") return false;
  const now = Date.now();
  const lastId = localStorage.getItem("schoolhopLastSoundNotificationId");
  const lastAt = Number(localStorage.getItem("schoolhopLastSoundAt") || 0);
  if (notificationId && notificationId === lastId && now - lastAt < 5000) return false;
  try {
    if (!notificationAudioElement) {
      notificationAudioElement = new Audio(notificationToneUrl());
      notificationAudioElement.preload = "auto";
    }
    notificationAudioElement.currentTime = 0;
    await notificationAudioElement.play();
    localStorage.setItem("schoolhopLastSoundNotificationId", notificationId);
    localStorage.setItem("schoolhopLastSoundAt", String(now));
    return true;
  } catch (audioError) {
    console.warn("[notification-audio] audio.play() rejected", audioError);
  }
  if (!("AudioContext" in window) && !("webkitAudioContext" in window)) return false;
  try {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    notificationAudioContext ||= new AudioContextCtor();
    const context = notificationAudioContext;
    await context.resume();
    const gain = context.createGain();
    const first = context.createOscillator();
    const second = context.createOscillator();
    const start = context.currentTime;
    first.type = "sine";
    second.type = "sine";
    first.frequency.setValueAtTime(880, start);
    second.frequency.setValueAtTime(660, start);
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.18, start + 0.03);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.55);
    first.connect(gain).connect(context.destination);
    second.connect(gain);
    first.start(start);
    first.stop(start + 0.26);
    second.start(start + 0.26);
    second.stop(start + 0.56);
    localStorage.setItem("schoolhopLastSoundNotificationId", notificationId);
    localStorage.setItem("schoolhopLastSoundAt", String(now));
    return true;
  } catch (error) {
    console.warn("[notification-audio] Notification sound failed", error);
    return false;
  }
}

async function deleteNotification(notificationId) {
  if (!notificationId) return;
  const previous = state.notifications;
  state.notifications = state.notifications.filter((notification) => notification.id !== notificationId);
  renderNotifications();
  try {
    await api(`/api/notifications/${notificationId}`, { method: "DELETE" });
    toast("Notification deleted");
  } catch (error) {
    state.notifications = previous;
    renderNotifications();
    toast(error.message);
  }
}

async function clearReadNotifications() {
  const previous = state.notifications;
  state.notifications = state.notifications.filter((notification) => !notification.read_at);
  renderNotifications();
  try {
    await api("/api/notifications/read", { method: "DELETE" });
    toast("Read notifications cleared");
  } catch (error) {
    state.notifications = previous;
    renderNotifications();
    toast(error.message);
  }
}

function formatHomeAddress(child) {
  return [child.home_address, child.home_city, child.home_postcode].filter(Boolean).join(", ");
}

function updateTripInState(updatedTrip) {
  if (!updatedTrip || !updatedTrip.id) return;
  const index = state.trips.findIndex((trip) => trip.id === updatedTrip.id);
  if (index >= 0) {
    state.trips[index] = updatedTrip;
  } else if (!isArchivedTrip(updatedTrip)) {
    state.trips.push(updatedTrip);
    state.trips.sort((a, b) => `${a.service_date} ${a.expected_time}`.localeCompare(`${b.service_date} ${b.expected_time}`));
  }
  if (isArchivedTrip(updatedTrip)) {
    if (state.trackingTripId === updatedTrip.id) stopTracking();
    state.trips = state.trips.filter((trip) => trip.id !== updatedTrip.id);
    if (state.selectedTripId === updatedTrip.id) {
      rememberSelectedTrip(null);
      chooseSelectedTrip();
    }
  }
}

async function refreshNotificationsAndPendingActions() {
  const [notifications, pendingActions] = await Promise.all([
    api("/api/notifications"),
    api("/api/pending-actions"),
  ]);
  setNotifications(notifications);
  state.pendingActions = pendingActions;
}

async function withButtonLock(button, callback) {
  if (button && button.disabled) return undefined;
  if (button) button.disabled = true;
  try {
    return await callback();
  } finally {
    if (button) button.disabled = false;
  }
}

function clearPendingInvite(token) {
  if (!token || state.pendingInviteToken !== token) return;
  state.pendingInviteToken = null;
  localStorage.removeItem("schoolhop_pending_invite");
  const url = new URL(window.location.href);
  if (url.searchParams.get("invite") === token) {
    url.searchParams.delete("invite");
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }
}

async function declineInvitation() {
  const token = $("acceptInviteForm").elements.token.value.trim();
  await declineInvitationToken(token);
}

async function declineInvitationToken(token) {
  if (!token) return toast("Enter an invitation code first");
  if (!window.confirm("Decline this carpool invitation?")) return;
  try {
    await api(`/api/invitations/${encodeURIComponent(token)}/decline`, { method: "POST", body: "{}" });
    state.pendingActions = state.pendingActions.filter((action) => action.type !== "invitation" || action.action_url !== `/?invite=${token}`);
    clearPendingInvite(token);
    $("acceptInviteForm").reset();
    renderAll();
    toast("Invitation declined");
  } catch (error) {
    toast(error.message);
  }
}

async function acceptInvitationToken(token) {
  if (!token) return toast("Invitation code is missing");
  try {
    const result = await api(`/api/invitations/${encodeURIComponent(token)}/accept`, { method: "POST", body: "{}" });
    state.pendingActions = state.pendingActions.filter((action) => action.type !== "invitation" || action.action_url !== `/?invite=${token}`);
    clearPendingInvite(token);
    await refresh();
    toast(result && result.status ? "Invitation accepted" : "Joined carpool");
  } catch (error) {
    toast(error.message);
  }
}

async function saveChild(form) {
  const data = formJSON(form);
  const childId = data.child_id;
  delete data.child_id;
  if (!data.home_postcode || !/^\d{4}$/.test(data.home_postcode)) throw new Error("Enter a valid four-digit Australian postcode.");
  if (!data.home_country) data.home_country = "AU";
  if (childId) {
    await api(`/api/children/${childId}`, { method: "PATCH", body: JSON.stringify(data) });
    resetChildForm();
  } else {
    await api("/api/children", { method: "POST", body: JSON.stringify(data) });
  }
}

async function deleteAccount() {
  const confirmation = window.prompt("Type DELETE to permanently delete your SchoolHop account.");
  if (confirmation == null) return;
  try {
    await api("/api/me", { method: "DELETE", body: JSON.stringify({ confirmation }) });
    stopTracking();
    localStorage.removeItem("schoolhop_token");
    localStorage.removeItem("schoolhop_pending_invite");
    sessionStorage.removeItem("schoolhop_selected_trip");
    sessionStorage.removeItem("schoolhop_selected_roster_driver");
    state.token = null;
    state.me = null;
    state.pendingInviteToken = null;
    await refresh();
    toast("Account deleted");
  } catch (error) {
    toast(error.message);
  }
}

function resetChildForm() {
  $("childForm").reset();
  $("childForm").elements.child_id.value = "";
  $("childFormTitle").textContent = "Add child";
  $("cancelChildEdit").classList.add("hidden");
}

async function leaveGroup() {
  const groupId = selectedGroupId();
  if (!groupId) return toast("No active carpool");
  if (!window.confirm("Leave this carpool?")) return;
  try {
    await api(`/api/groups/${groupId}/leave`, { method: "POST", body: "{}" });
    await refresh();
    toast("Left carpool");
  } catch (error) {
    toast(error.message);
  }
}

async function transferOwnership() {
  const groupId = selectedGroupId();
  const newCreatorId = $("transferOwnerSelect").value;
  if (!groupId || !newCreatorId) return toast("Choose a family member first.");
  const label = $("transferOwnerSelect").selectedOptions[0] ? $("transferOwnerSelect").selectedOptions[0].textContent : "this member";
  if (!window.confirm(`Transfer carpool ownership to ${label}?`)) return;
  try {
    await api(`/api/groups/${groupId}/transfer-ownership`, {
      method: "POST",
      body: JSON.stringify({ new_creator_user_id: newCreatorId }),
    });
    await refresh();
    toast("Ownership transferred");
  } catch (error) {
    toast(error.message);
  }
}

async function driverAssignmentDecision(tripId, status, button = null) {
  if (!tripId) return toast("Trip is missing");
  rememberSelectedTrip(tripId);
  if ($("activeTrip")) $("activeTrip").value = tripId;
  await tripAction("responses", { status }, button);
}

async function tripAction(path, body = {}, button = null) {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  return withButtonLock(button, async () => {
    const result = await api(`/api/trips/${trip.id}/${path}`, { method: "POST", body: JSON.stringify(body) });
    if (path === "responses") {
      trip.my_response = result;
      state.pendingActions = state.pendingActions.filter((action) => action.type !== "driver_assignment" || action.trip_id !== trip.id);
      renderAll();
      toast("Trip updated");
      return;
    }
    await refreshNotificationsAndPendingActions();
    renderAll();
    toast("Trip updated");
  }).catch((error) => toast(error.message));
}

async function reportDelay() {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  if (!isDriver(trip) || !isActionableTrip(trip)) return toast("Only the assigned driver can report a delay for an active or upcoming trip.");
  if (!window.confirm("Report a 10-minute delay for this trip? Affected parents and the organiser will be notified.")) return;
  try {
    const updated = await api(`/api/trips/${trip.id}/delay`, {
      method: "POST",
      body: JSON.stringify({ minutes: 10, note: "Driver reported a 10-minute delay." }),
    });
    updateTripInState(updated);
    renderAll();
    toast("Delay reported");
  } catch (error) {
    toast(error.message);
  }
}

async function showSwapPanel() {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  if (!isDriver(trip) || !isActionableTrip(trip)) return toast("Only the assigned driver can request a swap for an active or upcoming trip.");
  try {
    const drivers = await api(`/api/trips/${trip.id}/eligible-swap-drivers`);
    fillSelect($("swapDriver"), drivers, (driver) => `${driver.name} (${driver.email})`);
    $("confirmSwap").disabled = !drivers.length;
    $("swapPanel").classList.remove("hidden");
    if (!drivers.length) toast("No approved replacement drivers are available for every child on this trip.");
  } catch (error) {
    toast(error.message);
  }
}

async function requestSwap() {
  const trip = selectedTrip();
  const proposedDriverId = $("swapDriver").value;
  if (!trip || !proposedDriverId) return toast("Choose an approved replacement driver.");
  const label = $("swapDriver").selectedOptions[0] ? $("swapDriver").selectedOptions[0].textContent : "this driver";
  if (!window.confirm(`Request ${label} to cover this trip? The trip will not be reassigned unless the swap is confirmed.`)) return;
  try {
    await withButtonLock($("confirmSwap"), async () => {
    const request = await api(`/api/trips/${trip.id}/change-requests`, {
      method: "POST",
      body: JSON.stringify({
        request_type: "swap",
        proposed_driver_user_id: proposedDriverId,
        note: "Assigned driver requested a trip swap.",
      }),
    });
    state.changeRequests = [request, ...state.changeRequests.filter((item) => item.id !== request.id)];
    $("swapPanel").classList.add("hidden");
    renderAll();
    });
    toast("Swap request sent");
  } catch (error) {
    toast(error.message);
  }
}

async function decideSwapRequest(requestId, status, button = null) {
  try {
    await withButtonLock(button, async () => {
    const result = await api(`/api/roster-change-requests/${requestId}/decision`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    const request = swapRequestById(requestId);
    if (request) request.status = result.status;
    state.pendingActions = state.pendingActions.filter((action) => action.type !== "swap_request" || action.id !== requestId);
    if (request && status === "accepted") {
      const updatedTrip = await api(`/api/trips/${request.trip_id}`);
      updateTripInState(updatedTrip);
    }
    renderAll();
    });
    toast(`Swap ${status}`);
  } catch (error) {
    toast(error.message);
  }
}

async function deleteRoster(tripId) {
  const trip = state.trips.find((item) => item.id === tripId);
  const label = trip ? `${trip.service_date} ${trip.expected_time} ${trip.trip_type}` : "this roster";
  if (!window.confirm(`Delete ${label}? This is only allowed before any driver accepts.`)) return;
  await api(`/api/trips/${tripId}`, { method: "DELETE" });
  if (state.selectedTripId === tripId) {
    rememberSelectedTrip(null);
  }
  await refreshGroupContext();
  toast("Roster deleted");
}

async function startTrip() {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  if (!hasAcceptedTrip(trip)) return toast("Accept this trip before starting it.");
  if (!isTodayTrip(trip)) return toast("Trips can only be started on their service date.");
  try {
    await withButtonLock($("startTrip"), async () => {
    if (trip.status !== "started") {
      const started = await api(`/api/trips/${trip.id}/start`, { method: "POST", body: "{}" });
      updateTripInState(started);
      rememberSelectedTrip(started.id);
    }
    await startTracking(state.selectedTripId || trip.id);
    renderAll();
    });
    toast("Trip started. GPS tracking is active.");
  } catch (error) {
    toast(error.message);
  }
}

async function endTrip() {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  if (!isDriver(trip) && !window.confirm("End this driver's active trip as carpool admin? This stops GPS sharing but does not complete outstanding child handovers.")) return;
  try {
    await withButtonLock($("endTrip"), async () => {
    const ended = await api(`/api/trips/${trip.id}/end`, { method: "POST", body: "{}" });
    stopTracking();
    updateTripInState(ended);
    renderAll();
    });
    toast("Trip ended");
  } catch (error) {
    toast(error.message);
  }
}

async function startTracking(tripId) {
  if (isNativeApp) {
    await startNativeTracking(tripId);
    state.trackingTripId = tripId;
    state.watchId = "native";
    renderTripConsole();
    return;
  }
  if (!("geolocation" in navigator)) throw new Error("Geolocation is not supported in this browser");
  stopTracking();
  state.trackingTripId = tripId;
  state.watchId = navigator.geolocation.watchPosition(
    (position) => postPosition(tripId, position).catch((error) => toast(error.message)),
    (error) => toast(error.message),
    { enableHighAccuracy: true, maximumAge: 8000, timeout: 15000 },
  );
  renderTripConsole();
}

function stopTracking() {
  if (state.watchId === "native" || (isNativeApp && nativePlatform() === "android")) {
    stopNativeTracking().catch((error) => reportClientError(error, { source: "native-location-stop" }));
  } else if (state.watchId !== null) {
    navigator.geolocation.clearWatch(state.watchId);
  }
  state.watchId = null;
  state.trackingTripId = null;
  if (state.locationPostTimer !== null) window.clearTimeout(state.locationPostTimer);
  state.locationPostTimer = null;
  state.queuedLocationPost = null;
  renderTripConsole();
}

async function stopTrackingForLogout() {
  if (isNativeApp && nativePlatform() === "android") {
    await stopNativeTracking();
    clearLocalTrackingState();
  } else {
    stopTracking();
  }
}

function reconcileLocationPolling(trip) {
  if (trip && trip.status === "started" && state.trackingTripId !== trip.id) {
    startLocationPolling(trip.id);
  } else {
    stopLocationPolling();
  }
}

function startLocationPolling(tripId) {
  if (state.locationPollTripId === tripId && state.locationPollId !== null) return;
  stopLocationPolling();
  state.locationPollTripId = tripId;
  fetchLatestLocation(tripId);
  state.locationPollId = window.setInterval(() => fetchLatestLocation(tripId), 5000);
}

function stopLocationPolling() {
  if (state.locationPollId !== null) window.clearInterval(state.locationPollId);
  state.locationPollId = null;
  state.locationPollTripId = null;
  state.locationPollInFlight = false;
  state.routePollInFlight = false;
}

async function fetchLatestLocation(tripId) {
  if (state.locationPollInFlight) return;
  state.locationPollInFlight = true;
  try {
    const data = await api(`/api/trips/${tripId}/location`);
    if (data && data.location) updateTripLocation(tripId, data.location, false);
    const trip = state.trips.find((item) => item.id === tripId);
    if (trip && shouldFetchRoute(trip)) await fetchTripRoute(tripId);
  } catch (error) {
    reportClientError(error, { source: "location-poll" });
  } finally {
    state.locationPollInFlight = false;
  }
}

function shouldFetchRoute(trip) {
  if (!trip || trip.status !== "started") return false;
  return Date.now() >= Number(trip.next_route_refresh_at || 0);
}

async function fetchTripRoute(tripId, { force = false } = {}) {
  const trip = state.trips.find((item) => item.id === tripId);
  if (!trip || trip.status !== "started" || state.routePollInFlight) return;
  if (!force && !shouldFetchRoute(trip)) return;
  state.routePollInFlight = true;
  trip.next_route_refresh_at = Date.now() + ROUTE_REFRESH_MS;
  try {
    const data = await api(`/api/trips/${tripId}/route`);
    updateTripRoute(tripId, data);
  } catch (error) {
    updateTripRoute(tripId, { route: null, message: "ETA temporarily unavailable" });
    reportClientError(error, { source: "route-poll" });
  } finally {
    state.routePollInFlight = false;
  }
}

async function sendCurrentLocation(showToast) {
  const trip = selectedTrip();
  if (!trip) throw new Error("Select a trip first");
  if (isNativeApp && nativePlatform() === "android") {
    const { SchoolHopLocation } = nativePlugins();
    if (!SchoolHopLocation) throw new Error("Native location service is unavailable");
    const status = await SchoolHopLocation.status();
    if (!status.tracking || status.tripId !== trip.id) throw new Error("Start GPS sharing for this trip first");
    await SchoolHopLocation.requestLocationUpdate();
    if (showToast) toast("GPS update requested");
    return;
  }
  if (!("geolocation" in navigator)) throw new Error("Geolocation is not supported in this browser");
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(async (position) => {
      try {
        await postPosition(trip.id, position, { force: true });
        if (showToast) toast("Location sent");
        resolve();
      } catch (error) {
        reject(error);
      }
    }, reject, { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 });
  });
}

async function postPosition(tripId, position, { force = false } = {}) {
  const now = Date.now();
  if (force) {
    if (state.locationPostTimer !== null) window.clearTimeout(state.locationPostTimer);
    state.locationPostTimer = null;
    state.queuedLocationPost = null;
  } else if (state.locationPostInFlight || now - state.lastLocationPostAt < 4000) {
    queueLatestPosition(tripId, position);
    return;
  }
  await sendPositionNow(tripId, position);
}

function queueLatestPosition(tripId, position) {
  state.queuedLocationPost = { tripId, position };
  if (state.locationPostTimer !== null) return;
  const delay = Math.max(0, 4000 - (Date.now() - state.lastLocationPostAt));
  state.locationPostTimer = window.setTimeout(() => {
    state.locationPostTimer = null;
    flushQueuedPosition().catch((error) => toast(error.message));
  }, delay);
}

async function flushQueuedPosition() {
  if (!state.queuedLocationPost || state.locationPostInFlight) return;
  const queued = state.queuedLocationPost;
  state.queuedLocationPost = null;
  await postPosition(queued.tripId, queued.position);
}

async function sendPositionNow(tripId, position) {
  state.locationPostInFlight = true;
  const now = Date.now();
  state.lastLocationPostAt = now;
  try {
    const location = await api(`/api/trips/${tripId}/locations`, {
      method: "POST",
      body: JSON.stringify({
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy_meters: position.coords.accuracy,
        speed_mps: position.coords.speed,
        heading_degrees: position.coords.heading,
        recorded_at: new Date(position.timestamp).toISOString(),
      }),
    });
    updateTripLocation(tripId, location);
    await fetchTripRoute(tripId, { force: true });
  } finally {
    state.locationPostInFlight = false;
    if (state.queuedLocationPost && state.locationPostTimer === null) queueLatestPosition(state.queuedLocationPost.tripId, state.queuedLocationPost.position);
  }
}

function updateTripLocation(tripId, location, justUpdated = true) {
  const trip = state.trips.find((item) => item.id === tripId);
  if (!trip || !location) return;
  trip.latest_location = {
    ...location,
    fresh: location.fresh !== undefined ? location.fresh : true,
    message: justUpdated ? "GPS just updated" : location.message,
  };
  if (selectedTrip() && selectedTrip().id === tripId) renderTripConsole();
  renderHome();
}

function updateTripRoute(tripId, data) {
  const trip = state.trips.find((item) => item.id === tripId);
  if (!trip) return;
  trip.latest_route = data && data.route ? data.route : null;
  trip.route_message = data && data.message ? data.message : null;
  trip.route_reason = data && data.reason ? data.reason : null;
  if (selectedTrip() && selectedTrip().id === tripId) renderTripConsole();
  renderHome();
}

async function onHandoverClick(event) {
  const button = event.target instanceof Element ? event.target.closest("button[data-child]") : null;
  if (!button) return;
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  try {
    await withButtonLock(button, async () => api(`/api/trips/${trip.id}/children/${button.dataset.child}/handover`, {
      method: "POST",
      body: JSON.stringify({ handover_type: button.dataset.type }),
    }));
    const updatedTrip = await api(`/api/trips/${trip.id}`);
    updateTripInState(updatedTrip);
    renderAll();
    toast("Handover recorded");
  } catch (error) {
    toast(error.message);
  }
}

async function onDocumentClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const deleteNotificationButton = target.closest(".notification-delete");
  if (deleteNotificationButton) {
    await deleteNotification(deleteNotificationButton.dataset.notification);
    return;
  }
  const copyInvite = target.closest(".copy-invite");
  if (copyInvite) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(copyInvite.dataset.link);
    } else {
      window.prompt("Copy invite link", copyInvite.dataset.link);
    }
    toast("Invite link copied");
    return;
  }
  const acceptPendingInvite = target.closest(".accept-pending-invite");
  if (acceptPendingInvite) {
    await acceptInvitationToken(acceptPendingInvite.dataset.token);
    return;
  }
  const declinePendingInvite = target.closest(".decline-pending-invite");
  if (declinePendingInvite) {
    await declineInvitationToken(declinePendingInvite.dataset.token);
    return;
  }
  const acceptAssignment = target.closest(".accept-driver-assignment");
  if (acceptAssignment) {
    await driverAssignmentDecision(acceptAssignment.dataset.trip, "accepted", acceptAssignment);
    return;
  }
  const declineAssignment = target.closest(".decline-driver-assignment");
  if (declineAssignment) {
    await driverAssignmentDecision(declineAssignment.dataset.trip, "declined", declineAssignment);
    return;
  }
  const notification = target.closest(".notification-action");
  if (notification && !target.closest("button, a, input, select, textarea")) {
    await openActionUrl(notification.dataset.url || "");
    return;
  }
  const swapDecision = target.closest(".decide-swap");
  if (swapDecision) {
    await decideSwapRequest(swapDecision.dataset.request, swapDecision.dataset.status, swapDecision);
    return;
  }
  const deleteRosterButton = target.closest(".delete-roster");
  if (deleteRosterButton) {
    await deleteRoster(deleteRosterButton.dataset.trip);
    return;
  }
  const chooseTrip = target.closest(".choose-trip");
  if (chooseTrip) {
    rememberSelectedTrip(chooseTrip.dataset.trip);
    if ($("activeTrip")) $("activeTrip").value = state.selectedTripId;
    setTab("trip");
    renderAll();
    return;
  }
  const trackingTrip = target.closest(".trip-card[data-tracking='on']");
  if (trackingTrip && !target.closest("button, a, input, select, textarea")) {
    rememberSelectedTrip(trackingTrip.dataset.trip);
    if ($("activeTrip")) $("activeTrip").value = state.selectedTripId;
    setTab("trip");
    renderAll();
    return;
  }
  const editChild = target.closest(".edit-child");
  if (editChild) {
    const child = state.children.find((item) => item.id === editChild.dataset.child);
    if (!child) return;
    const form = $("childForm");
    Object.entries(child).forEach(([key, value]) => {
      if (form.elements[key]) form.elements[key].value = value || "";
    });
    form.elements.child_id.value = child.id;
    $("childFormTitle").textContent = "Edit child";
    $("cancelChildEdit").classList.remove("hidden");
    setTab("children");
    return;
  }
  const removeChild = target.closest(".remove-child");
  if (removeChild) {
    await api(`/api/children/${removeChild.dataset.child}`, { method: "DELETE" });
    await refresh();
    toast("Child removed");
    return;
  }
  const removeMember = target.closest(".remove-member");
  if (removeMember) {
    if (!window.confirm("Remove this family from the carpool?")) return;
    await api(`/api/groups/${selectedGroupId()}/members/${removeMember.dataset.user}/remove`, { method: "POST", body: "{}" });
    await refresh();
    toast("Family removed");
    return;
  }
  const accessDecision = target.closest(".approve-access, .decline-access");
  if (accessDecision) {
    const status = accessDecision.classList.contains("approve-access") ? "approved" : "declined";
    await api(`/api/groups/${selectedGroupId()}/access-requests/${accessDecision.dataset.request}/decision`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    await refresh();
    toast(`Access request ${status}`);
  }
}

function onNotificationPointerDown(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target || target.closest("button, a, input, select, textarea")) return;
  const card = target.closest(".notification-card");
  if (!card || event.pointerType === "mouse") return;
  state.notificationSwipe = {
    id: card.dataset.notification,
    startX: event.clientX,
    startY: event.clientY,
    card,
  };
}

async function onNotificationPointerUp(event) {
  if (!state.notificationSwipe) return;
  const swipe = state.notificationSwipe;
  resetNotificationSwipe();
  const deltaX = event.clientX - swipe.startX;
  const deltaY = event.clientY - swipe.startY;
  if (deltaX < -70 && Math.abs(deltaY) < 60) {
    swipe.card.classList.add("swiped");
    await deleteNotification(swipe.id);
  }
}

function resetNotificationSwipe() {
  state.notificationSwipe = null;
}

async function openActionUrl(actionUrl) {
  if (!actionUrl) return;
  const url = new URL(actionUrl, window.location.origin);
  const trustedOrigins = [window.location.origin, nativeApiBaseUrl, "https://schoolhop.shourish.com"].filter(Boolean);
  if (url.username || url.password || (url.protocol !== "schoolhop:" && (!trustedOrigins.includes(url.origin) || url.pathname !== "/"))) return;
  const invite = url.searchParams.get("invite");
  if (invite) {
    state.pendingInviteToken = invite;
    localStorage.setItem("schoolhop_pending_invite", invite);
    const input = document.querySelector("#acceptInviteForm input[name='token']");
    if (input) input.value = invite;
    state.pendingTab = "group";
    if (state.me) setTab("group");
    return;
  }
  const tripId = url.searchParams.get("trip");
  const swapRequestId = url.searchParams.get("swap_request");
  if (tripId) {
    rememberSelectedTrip(tripId);
    if ($("activeTrip")) $("activeTrip").value = state.selectedTripId;
    state.pendingSwapRequestId = swapRequestId;
  }
  const tab = url.searchParams.get("tab") || (tripId ? "trip" : "alerts");
  if (!["home", "group", "roster", "trip", "alerts", "account"].includes(tab)) return;
  state.pendingTab = tab;
  if (state.me) await refreshGroupContext();
}

function wireNativeEvents() {
  if (!isNativeApp) return;
  const { App, PushNotifications } = nativePlugins();
  if (App && typeof App.addListener === "function") {
    App.addListener("appStateChange", (event) => {
      if (event && event.isActive && state.token) {
        registerNativePushToken(false).catch(() => reportClientError(new Error("Push registration failed"), { source: "native-push-resume" }));
      }
    });
    App.addListener("appUrlOpen", (event) => {
      if (event && event.url) openActionUrl(event.url).catch((error) => reportClientError(error, { source: "native-url-open" }));
    });
  }
  if (PushNotifications && typeof PushNotifications.addListener === "function") {
    PushNotifications.addListener("pushNotificationReceived", () => {
      if (state.token && !state.nativePushSigningOut) refreshGroupContext().catch(() => {});
    });
    PushNotifications.addListener("pushNotificationActionPerformed", (event) => {
      const data = event && event.notification && event.notification.data ? event.notification.data : {};
      const actionUrl = data.action_url || data.url || "";
      if (actionUrl) openActionUrl(actionUrl).catch((error) => reportClientError(error, { source: "native-push-action" }));
    });
  }
  const { SchoolHopLocation } = nativePlugins();
  if (SchoolHopLocation && typeof SchoolHopLocation.addListener === "function") {
    SchoolHopLocation.addListener("trackingStopped", (event) => {
      clearLocalTrackingState();
      refreshGroupContext().catch((error) => reportClientError(error, { source: "native-location-stopped", status: event && event.status }));
      toast("Location sharing stopped for this trip.");
    });
    SchoolHopLocation.addListener("trackingError", (event) => {
      const message = event && event.message ? event.message : "Native location tracking failed";
      reportClientError(new Error(message), { source: "native-location-tracking" });
    });
  }
}

async function registerNativePushToken(requestPermission = true) {
  if (!isNativeApp || !state.token || state.nativePushSigningOut) return;
  if (nativePushRegistration) return nativePushRegistration;
  nativePushRegistration = configureNativePush(requestPermission);
  try { await nativePushRegistration; } finally { nativePushRegistration = null; }
}

async function configureNativePush(requestPermission) {
  const { PushNotifications } = nativePlugins();
  if (!PushNotifications) return;
  if (nativePlatform() === "android" && !(await androidFirebaseMessagingConfigured())) return;
  if (!state.nativeNotificationChannelsCreated && nativePlatform() === "android" && typeof PushNotifications.createChannel === "function") {
    await PushNotifications.createChannel({
      id: "schoolhop_activity",
      name: "SchoolHop activity",
      description: "Carpool assignments, trip updates, swaps, delays, and handovers.",
      importance: 4,
      visibility: 0,
    });
    state.nativeNotificationChannelsCreated = true;
  }
  const permission = requestPermission ? await PushNotifications.requestPermissions() : await PushNotifications.checkPermissions();
  if (!permission || permission.receive !== "granted" || !state.token || state.nativePushSigningOut) return;
  if (!state.nativePushListenerRegistered) {
    await PushNotifications.addListener("registration", (token) => {
      const value = token && (token.value || token.token);
      if (!value || !state.token || state.nativePushSigningOut) return;
      const authorization = `Bearer ${state.token}`;
      const device = {
        provider: nativePlatform() === "android" ? "fcm" : "apns",
        token: value,
        platform: nativePlatform() === "android" ? "android" : "ios",
      };
      // Serialize token refresh and Sign Out; never register for a cleared account.
      nativePushQueue = nativePushQueue.catch(() => {}).then(async () => {
        let devices = JSON.parse(localStorage.getItem("schoolhop_push_devices") || "[]");
        const previous = devices.filter((saved) => saved.provider !== device.provider || saved.token !== device.token);
        devices = [...previous, device];
        // Keep failed registrations/removals for retry and Sign Out cleanup.
        localStorage.setItem("schoolhop_push_devices", JSON.stringify(devices));
        await api("/api/mobile/devices", { method: "POST", headers: { Authorization: authorization }, body: JSON.stringify(device) });
        for (const saved of previous) {
          await api("/api/mobile/devices", { method: "DELETE", headers: { Authorization: authorization }, body: JSON.stringify(saved) });
          devices = devices.filter((item) => item.provider !== saved.provider || item.token !== saved.token);
          localStorage.setItem("schoolhop_push_devices", JSON.stringify(devices));
        }
      });
      nativePushQueue.catch(() => reportClientError(new Error("Push token registration failed"), { source: "native-push-registration" }));
      return nativePushQueue.catch(() => {});
    });
    await PushNotifications.addListener("registrationError", () => {
      reportClientError(new Error("Push notification registration failed"), { source: "native-push-registration" });
    });
    state.nativePushListenerRegistered = true;
  }
  if (!state.nativePushSigningOut && state.token) await PushNotifications.register();
}

async function unregisterNativePushToken() {
  if (!isNativeApp) return;
  state.nativePushSigningOut = true;
  try {
    if (nativePushRegistration) await nativePushRegistration;
    await nativePushQueue.catch(() => {});
    const devices = JSON.parse(localStorage.getItem("schoolhop_push_devices") || "[]");
    for (const device of devices) {
      await api("/api/mobile/devices", { method: "DELETE", body: JSON.stringify(device) });
    }
    const { PushNotifications } = nativePlugins();
    if (PushNotifications) {
      await PushNotifications.unregister();
      await PushNotifications.removeAllDeliveredNotifications();
    }
    localStorage.removeItem("schoolhop_push_devices");
  } catch (error) {
    state.nativePushSigningOut = false;
    throw error;
  }
}

async function androidFirebaseMessagingConfigured() {
  if (state.androidFirebaseMessagingConfigured !== null) return state.androidFirebaseMessagingConfigured;
  const { SchoolHopConfig } = nativePlugins();
  if (!SchoolHopConfig || typeof SchoolHopConfig.getFirebaseMessagingStatus !== "function") {
    state.androidFirebaseMessagingConfigured = false;
    return false;
  }
  try {
    const status = await SchoolHopConfig.getFirebaseMessagingStatus();
    state.androidFirebaseMessagingConfigured = Boolean(status && status.configured);
  } catch (error) {
    reportClientError(error, { source: "android-firebase-config" });
    state.androidFirebaseMessagingConfigured = false;
  }
  return state.androidFirebaseMessagingConfigured;
}

async function startNativeTracking(tripId) {
  const { SchoolHopLocation } = nativePlugins();
  if (!SchoolHopLocation) throw new Error("Native location service is unavailable");
  await SchoolHopLocation.startTripTracking({
    tripId,
    token: state.token,
    apiBaseUrl: nativeApiBaseUrl || window.location.origin,
  });
}

async function stopNativeTracking() {
  const { SchoolHopLocation } = nativePlugins();
  if (SchoolHopLocation) await SchoolHopLocation.stopTripTracking();
}

function clearLocalTrackingState() {
  state.watchId = null;
  state.trackingTripId = null;
  if (state.locationPostTimer !== null) window.clearTimeout(state.locationPostTimer);
  state.locationPostTimer = null;
  state.queuedLocationPost = null;
  renderTripConsole();
}

window.setInterval(() => {
  if (state.token && !$("app").classList.contains("hidden")) {
    refreshGroupContext().catch(() => undefined);
  }
}, 30000);

init().catch((error) => {
  reportClientError(error, { source: "init" });
  toast(error.message);
});

window.addEventListener("error", (event) => {
  reportClientError(event.error || new Error(event.message), { source: event.filename });
  toast(event.message || "Browser error while loading SchoolHop");
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason instanceof Error ? event.reason : new Error(String(event.reason || "Unhandled promise rejection"));
  reportClientError(reason, { source: "unhandledrejection" });
  toast(reason.message);
});
