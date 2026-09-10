const titleEl = document.getElementById("title");
const detailEl = document.getElementById("detail");
const errEl = document.getElementById("error");
const nextEl = document.getElementById("next");
const passEl = document.getElementById("pass");
const slate = passEl.querySelector(".clap-slate");
const logEl = document.getElementById("log");
const logWrap = document.getElementById("log-wrap");
const diagnosticsEl = document.getElementById("diagnostics");
let studioHandoffStarted = false;

function friendlyStatus(state) {
  if (state.phase === "need_names") return "Ready for you to enter.";
  if (state.phase === "ready") return "Studio is ready.";
  if (state.phase === "need_python") return "One setup step is needed before Studio can open.";
  if (state.phase === "error") return "Studio did not open. Try again or download diagnostics.";
  if (state.phase === "starting") return "Getting Studio ready.";
  return "Checking Studio.";
}

function paint(state) {
  document.body.dataset.phase = state.phase || "opening";
  titleEl.textContent = state.title || "Opening the desk";
  detailEl.textContent = state.detail || "";
  const err = state.phase === "error" ? state.error || state.detail : "";
  errEl.hidden = !err;
  errEl.textContent = err && state.phase === "error" ? err : "";
  diagnosticsEl.classList.toggle("hidden", state.phase !== "error");
  const installed = state.runtime_mode === "installed";

  logWrap.classList.remove("hidden");
  logEl.textContent = friendlyStatus(state);

  if (state.phase === "ready") {
    passEl.href = "#";
    passEl.removeAttribute("target");
    passEl.classList.add("is-wait");
    slate.textContent = "Wait";
    nextEl.textContent = "Opening Studio here.";
    if (!studioHandoffStarted) {
      studioHandoffStarted = true;
      window.location.replace(state.studio || "http://studio.diony.local");
    }
    return;
  } else if (state.phase === "need_python") {
    passEl.href = state.error || "https://www.python.org/downloads/";
    passEl.target = "_blank";
    passEl.rel = "noopener";
    passEl.classList.remove("is-wait");
    slate.textContent = "Get Python";
    nextEl.textContent = "Install it, then reopen Diony AI.";
  } else if (state.phase === "need_names") {
    passEl.href = "#ensure";
    passEl.removeAttribute("target");
    passEl.classList.remove("is-wait");
    slate.textContent = "Continue";
    nextEl.textContent = "Allow the local Studio address, then this page will continue.";
  } else if (state.phase === "error") {
    passEl.href = window.location.href;
    passEl.removeAttribute("target");
    passEl.classList.remove("is-wait");
    slate.textContent = "Try again";
    nextEl.textContent = installed
      ? "Download diagnostics, then reopen Diony AI."
      : "Keep Terminal open, or reopen Diony AI.";
  } else {
    passEl.href = "#";
    passEl.removeAttribute("target");
    passEl.classList.add("is-wait");
    slate.textContent = "Wait";
    nextEl.textContent = installed ? "Checking your studio." : "Keep Terminal open.";
  }
}

async function tick() {
  try {
    const res = await fetch("/api/status", { cache: "no-store" });
    paint(await res.json());
  } catch {
    titleEl.textContent = "Opening the desk";
    detailEl.textContent = "Waiting for the opener.";
  }
}

passEl.addEventListener("click", async (event) => {
  const label = slate.textContent || "";
  if (label === "Wait") {
    event.preventDefault();
    return;
  }
  if (document.body.dataset.phase !== "need_names") return;
  event.preventDefault();
  slate.textContent = "Wait";
  passEl.classList.add("is-wait");
  try {
    await fetch("/api/ensure-names", { cache: "no-store" });
  } catch {
    /* status poll picks up the password result */
  }
});

tick();
window.setInterval(tick, 800);
