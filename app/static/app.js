const state = {
  token: localStorage.getItem("schoolhop_token"),
  config: {},
  me: null,
  registration: { email: null, name: null, phone: null, token: null },
  schools: [],
  groups: [],
  children: [],
  group: null,
  groupChildren: [],
  trips: [],
  history: [],
  notifications: [],
  selectedTripId: localStorage.getItem("schoolhop_selected_trip"),
  watchId: null,
  trackingTripId: null,
};

const $ = (id) => document.getElementById(id);
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
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error((data && data.detail) || response.statusText);
  return data;
}

function reportClientError(error, context = {}) {
  fetch("/api/client-errors", {
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

function formJSON(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  Object.keys(data).forEach((key) => {
    if (typeof data[key] === "string") data[key] = data[key].trim();
    if (data[key] === "") data[key] = null;
  });
  return data;
}

function fillSelect(select, rows, label, value = "id") {
  if (!select) return;
  select.innerHTML = rows.length
    ? rows.map((row) => `<option value="${escapeHTML(row[value])}">${escapeHTML(label(row))}</option>`).join("")
    : `<option value="">None available</option>`;
}

function selectedGroupId() {
  const groupSelect = $("groupSelect");
  return (groupSelect && groupSelect.value) || (state.groups[0] && state.groups[0].id) || "";
}

function selectedTrip() {
  const activeTrip = $("activeTrip");
  const activeTripId = activeTrip ? activeTrip.value : "";
  return state.trips.find((trip) => trip.id === activeTripId) || state.trips.find((trip) => trip.id === state.selectedTripId) || state.trips[0] || null;
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
  if (["cancelled", "driver_unavailable", "declined"].includes(status)) return "bad";
  if (status === "delayed") return "warn";
  return "muted";
}

function pill(label, tone = "muted") {
  return `<span class="pill ${tone}">${escapeHTML(label)}</span>`;
}

function empty(message) {
  return `<div class="empty">${escapeHTML(message)}</div>`;
}

async function init() {
  state.config = await api("/api/config").catch(() => ({}));
  wireEvents();
  registerServiceWorker();
  hydrateInviteFromUrl();
  await refresh();
}

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/static/service-worker.js").catch(() => undefined);
  }
}

function hydrateInviteFromUrl() {
  const token = new URLSearchParams(window.location.search).get("invite");
  const input = document.querySelector("#acceptInviteForm input[name='token']");
  if (token && input) input.value = token;
}

function wireEvents() {
  $("showLogin").addEventListener("click", () => toggleAuth("login"));
  $("showRegister").addEventListener("click", () => toggleAuth("registerStart"));
  document.querySelectorAll(".tabs button").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));

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
    return api("/api/schools", { method: "POST", body: JSON.stringify(data) });
  });
  submit("groupForm", async (form) => api("/api/groups", { method: "POST", body: JSON.stringify(formJSON(form)) }));
  submit("acceptInviteForm", async (form) => api(`/api/invitations/${encodeURIComponent(formJSON(form).token)}/accept`, { method: "POST", body: "{}" }));
  submit("inviteForm", async (form) => {
    const invitation = await api(`/api/groups/${selectedGroupId()}/invitations`, { method: "POST", body: JSON.stringify(formJSON(form)) });
    toast(invitation.delivery_status === "sent" ? "Invite email sent" : `Invite token: ${invitation.token}`);
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
    data.child_ids = [...$("tripChildren").querySelectorAll("input:checked")].map((input) => input.value);
    if (!data.child_ids.length) throw new Error("Choose at least one child");
    return api(`/api/groups/${selectedGroupId()}/trips`, { method: "POST", body: JSON.stringify(data) });
  });

  $("groupSelect").addEventListener("change", () => refreshGroupContext().catch((error) => toast(error.message)));
  $("activeTrip").addEventListener("change", () => {
    state.selectedTripId = $("activeTrip").value;
    localStorage.setItem("schoolhop_selected_trip", state.selectedTripId);
    renderAll();
  });
  $("logout").addEventListener("click", () => {
    stopTracking();
    localStorage.removeItem("schoolhop_token");
    state.token = null;
    refresh();
  });
  $("refreshAll").addEventListener("click", () => refresh().catch((error) => toast(error.message)));
  $("leaveGroup").addEventListener("click", leaveGroup);
  $("cancelChildEdit").addEventListener("click", resetChildForm);
  $("acceptTrip").addEventListener("click", () => tripAction("responses", { status: "accepted" }));
  $("declineTrip").addEventListener("click", () => tripAction("responses", { status: "declined" }));
  $("startTrip").addEventListener("click", startTrip);
  $("endTrip").addEventListener("click", endTrip);
  $("sendLocation").addEventListener("click", () => sendCurrentLocation(true));
  $("handoverButtons").addEventListener("click", onHandoverClick);
  document.addEventListener("click", onDocumentClick);
}

function toggleAuth(mode) {
  $("showLogin").classList.toggle("active", mode === "login");
  $("showRegister").classList.toggle("active", mode !== "login");
  $("loginForm").classList.toggle("hidden", mode !== "login");
  $("registerStartForm").classList.toggle("hidden", mode !== "registerStart");
  $("registerVerifyForm").classList.toggle("hidden", mode !== "registerVerify");
  $("registerCompleteForm").classList.toggle("hidden", mode !== "registerComplete");
}

function setToken(token) {
  state.token = token;
  localStorage.setItem("schoolhop_token", token);
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
  state.me = (await api("/api/me")).user;
  $("sessionSummary").textContent = state.me.email;
  state.schools = await api("/api/schools");
  state.groups = await api("/api/groups");
  state.children = await api("/api/children");
  fillSelect($("schoolSelect"), state.schools, (s) => `${s.name}${s.city ? `, ${s.city}` : ""}`);
  fillSelect($("childSchoolSelect"), state.schools, (s) => `${s.name}${s.city ? `, ${s.city}` : ""}`);
  fillSelect($("groupSelect"), state.groups, (g) => `${g.name} - ${g.school_name}`);
  if (state.groups[0] && !$("groupSelect").value) $("groupSelect").value = state.groups[0].id;
  await refreshGroupContext();
}

async function refreshGroupContext() {
  const groupId = selectedGroupId();
  if (!groupId) {
    state.group = null;
    state.groupChildren = [];
    state.trips = [];
    state.history = [];
    state.notifications = state.token ? await api("/api/notifications") : [];
    renderAll();
    return;
  }
  state.group = await api(`/api/groups/${groupId}`);
  state.groupChildren = await api(`/api/groups/${groupId}/children`);
  state.trips = await api(`/api/groups/${groupId}/trips`);
  state.history = await api(`/api/groups/${groupId}/trips/history`);
  state.notifications = await api("/api/notifications");
  renderAll();
}

function renderAll() {
  renderHome();
  renderTrips();
  renderChildren();
  renderMore();
  renderNotifications();
}

function renderHome() {
  $("homeGroup").textContent = (state.group && state.group.group && state.group.group.name) || "None";
  $("homeTrips").textContent = String(state.trips.length);
  $("homeTracking").textContent = state.watchId === null ? "Off" : "Active";
  $("homeTripList").innerHTML = state.trips.length ? state.trips.slice(0, 4).map(tripCard).join("") : empty("No current or future trips.");
}

function renderTrips() {
  fillSelect($("tripDriver"), (state.group && state.group.members) || [], (m) => `${m.name} (${m.email})`);
  $("tripChildren").innerHTML = state.groupChildren.length ? state.groupChildren.map((child) => `
    <label><input type="checkbox" value="${escapeHTML(child.id)}" /> ${escapeHTML(child.name)} <span>${escapeHTML(child.parent_name)}</span></label>
  `).join("") : `<p>No group children available.</p>`;
  $("tripList").innerHTML = state.trips.length ? state.trips.map(tripCard).join("") : empty("No current or future trips.");
  $("historyTripList").innerHTML = state.history.length ? state.history.map(tripCard).join("") : empty("Completed and past trips appear here.");
  fillSelect($("activeTrip"), state.trips, (t) => `${t.service_date} ${t.expected_time} ${t.trip_type} - ${(t.driver && t.driver.name) || "driver"}`);
  if (state.selectedTripId && state.trips.some((trip) => trip.id === state.selectedTripId)) {
    $("activeTrip").value = state.selectedTripId;
  } else if (state.trips[0]) {
    state.selectedTripId = state.trips[0].id;
    $("activeTrip").value = state.selectedTripId;
  } else {
    state.selectedTripId = null;
    localStorage.removeItem("schoolhop_selected_trip");
  }
  renderTripConsole();
}

function tripCard(trip) {
  const driverLabel = (trip.driver && trip.driver.name) || "Assigned driver";
  const children = trip.children.map((child) => `${child.name}: pickup ${child.pickup_status}, drop-off ${child.dropoff_status}`).join("; ");
  const response = trip.my_response ? pill(trip.my_response.status, statusTone(trip.my_response.status)) : "";
  const selected = selectedTrip();
  const active = selected && selected.id === trip.id ? " selected" : "";
  return `
    <article class="item-card trip-card${active}" data-trip="${escapeHTML(trip.id)}">
      <div class="item-head">
        <div>
          <strong>${escapeHTML(trip.service_date)} · ${escapeHTML(trip.expected_time)}</strong>
          <span>${escapeHTML(trip.trip_type)} by ${escapeHTML(driverLabel)}</span>
        </div>
        <div>${pill(trip.status, statusTone(trip.status))}${response}</div>
      </div>
      <p>${escapeHTML(children || "No children visible.")}</p>
      <button class="ghost choose-trip" data-trip="${escapeHTML(trip.id)}" type="button">Open</button>
    </article>`;
}

function renderTripConsole() {
  const trip = selectedTrip();
  $("trackingBanner").className = `tracking ${state.watchId === null ? "off" : "on"}`;
  $("trackingBanner").textContent = state.watchId === null
    ? "Tracking off. Browser GPS starts only after the assigned driver starts this trip."
    : "Tracking active while this page remains open.";
  if (!trip) {
    $("acceptTrip").disabled = true;
    $("declineTrip").disabled = true;
    $("startTrip").disabled = true;
    $("endTrip").disabled = true;
    $("sendLocation").disabled = true;
    $("handoverButtons").innerHTML = empty("Select or create a trip.");
    return;
  }
  const driver = isDriver(trip);
  $("acceptTrip").disabled = !driver;
  $("declineTrip").disabled = !driver;
  $("startTrip").textContent = trip.status === "started" ? "Start GPS" : "Start trip";
  $("startTrip").disabled = !driver || !["planned", "delayed", "started"].includes(trip.status);
  $("endTrip").disabled = !driver || trip.status !== "started";
  $("sendLocation").disabled = !driver || trip.status !== "started";
  $("handoverButtons").innerHTML = trip.children.map((child) => `
    <article class="handover-card">
      <div>
        <strong>${escapeHTML(child.name)}</strong>
        <span>Pickup ${escapeHTML(child.pickup_status)} · Drop-off ${escapeHTML(child.dropoff_status)}</span>
      </div>
      <div class="mini-actions">
        <button data-child="${escapeHTML(child.child_id)}" data-type="picked_up" ${driver ? "" : "disabled"} type="button">Picked up</button>
        <button data-child="${escapeHTML(child.child_id)}" data-type="dropped_off" ${driver ? "" : "disabled"} type="button">Dropped off</button>
        <button class="ghost danger" data-child="${escapeHTML(child.child_id)}" data-type="absent" ${driver ? "" : "disabled"} type="button">Absent</button>
      </div>
    </article>`).join("");
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
  const detail = $("groupDetail");
  if (!state.group) {
    detail.innerHTML = empty("Create or join one active group.");
    $("leaveGroup").disabled = true;
    return;
  }
  $("leaveGroup").disabled = false;
  const members = state.group.members.map((m) => `
    <article class="small-card">
      <strong>${escapeHTML(m.name)}</strong>
      <span>${escapeHTML(m.email)} · ${escapeHTML(m.role)}</span>
      ${m.id !== state.me.id && state.group.group.created_by === state.me.id ? `<button class="ghost danger remove-member" data-user="${escapeHTML(m.id)}" type="button">Remove</button>` : ""}
    </article>`).join("");
  const invitations = state.group.invitations.map((inv) => `
    <article class="small-card">
      <strong>${escapeHTML(inv.email)}</strong>
      <span>${escapeHTML(inv.status)}</span>
    </article>`).join("");
  detail.innerHTML = `
    <div><h3>Members</h3><div class="stack">${members || empty("No members.")}</div></div>
    <div><h3>Invitations</h3><div class="stack">${invitations || empty("No invitations.")}</div></div>`;
}

function renderNotifications() {
  $("notificationList").innerHTML = state.notifications.length ? state.notifications.slice(0, 8).map((n) => `
    <article class="item-card">
      <div class="item-head"><strong>${escapeHTML(n.title)}</strong>${pill(n.kind)}</div>
      <p>${escapeHTML(n.body)}</p>
      <span>${escapeHTML(new Date(n.created_at).toLocaleString())}</span>
    </article>`).join("") : empty("No notifications yet.");
}

function formatHomeAddress(child) {
  return [child.home_address, child.home_city, child.home_country].filter(Boolean).join(", ");
}

async function saveChild(form) {
  const data = formJSON(form);
  const childId = data.child_id;
  delete data.child_id;
  if (!data.home_country) data.home_country = "DE";
  if (childId) {
    await api(`/api/children/${childId}`, { method: "PATCH", body: JSON.stringify(data) });
    resetChildForm();
  } else {
    await api("/api/children", { method: "POST", body: JSON.stringify(data) });
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
  if (!groupId) return toast("No active group");
  try {
    await api(`/api/groups/${groupId}/leave`, { method: "POST", body: "{}" });
    await refresh();
    toast("Left group");
  } catch (error) {
    toast(error.message);
  }
}

async function tripAction(path, body = {}) {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  try {
    await api(`/api/trips/${trip.id}/${path}`, { method: "POST", body: JSON.stringify(body) });
    await refreshGroupContext();
    toast("Trip updated");
  } catch (error) {
    toast(error.message);
  }
}

async function startTrip() {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  try {
    if (trip.status !== "started") {
      const started = await api(`/api/trips/${trip.id}/start`, { method: "POST", body: "{}" });
      state.selectedTripId = started.id;
    }
    await startTracking(state.selectedTripId || trip.id);
    await refreshGroupContext();
    toast("Trip started. GPS tracking is active.");
  } catch (error) {
    toast(error.message);
  }
}

async function endTrip() {
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  try {
    await api(`/api/trips/${trip.id}/end`, { method: "POST", body: "{}" });
    stopTracking();
    await refreshGroupContext();
    toast("Trip ended");
  } catch (error) {
    toast(error.message);
  }
}

async function startTracking(tripId) {
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
  if (state.watchId !== null) navigator.geolocation.clearWatch(state.watchId);
  state.watchId = null;
  state.trackingTripId = null;
  renderTripConsole();
}

async function sendCurrentLocation(showToast) {
  const trip = selectedTrip();
  if (!trip) throw new Error("Select a trip first");
  if (!("geolocation" in navigator)) throw new Error("Geolocation is not supported in this browser");
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(async (position) => {
      try {
        await postPosition(trip.id, position);
        if (showToast) toast("Location sent");
        resolve();
      } catch (error) {
        reject(error);
      }
    }, reject, { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 });
  });
}

async function postPosition(tripId, position) {
  await api(`/api/trips/${tripId}/locations`, {
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
}

async function onHandoverClick(event) {
  const button = event.target instanceof Element ? event.target.closest("button[data-child]") : null;
  if (!button) return;
  const trip = selectedTrip();
  if (!trip) return toast("Select a trip first");
  try {
    await api(`/api/trips/${trip.id}/children/${button.dataset.child}/handover`, {
      method: "POST",
      body: JSON.stringify({ handover_type: button.dataset.type }),
    });
    await refreshGroupContext();
    toast("Handover recorded");
  } catch (error) {
    toast(error.message);
  }
}

async function onDocumentClick(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (!target) return;
  const chooseTrip = target.closest(".choose-trip");
  if (chooseTrip) {
    state.selectedTripId = chooseTrip.dataset.trip;
    localStorage.setItem("schoolhop_selected_trip", state.selectedTripId);
    if ($("activeTrip")) $("activeTrip").value = state.selectedTripId;
    setTab("trips");
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
    await api(`/api/groups/${selectedGroupId()}/members/${removeMember.dataset.user}/remove`, { method: "POST", body: "{}" });
    await refresh();
    toast("Member removed");
  }
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
