// static/main.js
document.addEventListener("DOMContentLoaded", () => {
  const panels = {
    1: { pid: null, lastStatus: null },
    2: { pid: null, lastStatus: null },
    3: { pid: null, lastStatus: null },
    4: { pid: null, lastStatus: null },
  };

  // handle Run forms
  document.querySelectorAll(".panel-form").forEach(form => {
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const panel = form.getAttribute("data-panel");
      const input = form.querySelector(".pid-input");
      const pid = (input.value || "").trim();
      if (!pid) return alert("Nhập GPM Profile ID");

      panels[panel].pid = pid;
      panels[panel].lastStatus = null;

      setBadge(panel, "run", "starting...");
      setDebug(panel, "");
      setWS(panel, "");
      clearLog(panel);
      appendLog(panel, `▶️ Run profile: ${pid}`);

      const fd = new FormData();
      fd.append("profile_id", pid);
      try {
        const resp = await fetch("/start_profile", { method: "POST", body: fd });
        const j = await resp.json();
        if (!j.ok) {
          setBadge(panel, "err", "error");
          appendLog(panel, `❌ ${j.message || "Error"}`);
        } else {
          appendLog(panel, `⏳ ${j.message}`);
        }
      } catch (err) {
        setBadge(panel, "err", "error");
        appendLog(panel, `❌ Request failed: ${err}`);
      }
    });
  });

  // inject button (simple: only send profile_id; backend reads ./script.js)
  document.querySelectorAll(".inject-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const panel = btn.getAttribute("data-panel");
      const pid = panels[panel].pid;
      if (!pid) return alert("Panel chưa có Profile ID nào đã chạy.");

      appendLog(panel, "🚀 Inject bắt đầu (sử dụng ./script.js) ...");
      try {
        const fd = new FormData();
        fd.append("profile_id", pid);
        const resp = await fetch("/inject", { method: "POST", body: fd });
        const j = await resp.json();
        if (!j.ok) {
          appendLog(panel, `❌ Inject failed: ${j.message || "Unknown error"}`);
          setBadge(panel, "err", "inject error");
        } else {
          const s = j.stats || {};
          appendLog(panel, `✅ Inject xong. Ctx:${s.contexts||0} | Pages:${s.pages||0} | URL:${s.injected_url||0} | Inline:${s.injected_inline||0}`);
        }
      } catch (err) {
        appendLog(panel, `❌ Inject exception: ${err}`);
        setBadge(panel, "err", "inject error");
      }
    });
  });

  // clear log buttons
  document.querySelectorAll(".clear-log-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const panel = btn.getAttribute("data-panel");
      clearLog(panel);
    });
  });

  function setBadge(panel, type, text) {
    const el = document.getElementById(`p${panel}-status-badge`);
    el.className = "pill " + (type || "");
    el.textContent = text || "";
  }
  function setDebug(panel, txt) { document.getElementById(`p${panel}-debug`).textContent = txt || ""; }
  function setWS(panel, txt) { document.getElementById(`p${panel}-ws`).textContent = txt || ""; }
  function clearLog(panel) { document.getElementById(`p${panel}-log`).textContent = ""; }
  function appendLog(panel, line) {
    const box = document.getElementById(`p${panel}-log`);
    const now = new Date();
    const hh = now.toLocaleTimeString();
    box.textContent += `[${hh}] ${line}\n`;
    box.scrollTop = box.scrollHeight;
  }

  // poll per-panel if pid set
  async function pollPanel(panel) {
    const pid = panels[panel].pid;
    if (!pid) return;
    try {
      const r = await fetch(`/status/${encodeURIComponent(pid)}`);
      const info = await r.json();
      if (!info.exists) return;
      const st = (info.status || "unknown").toLowerCase();
      if (st.includes("error")) setBadge(panel, "err", "error");
      else if (st.startsWith("start")) setBadge(panel, "ok", "started");
      else if (st.includes("queued")) setBadge(panel, "run", "queued");
      else setBadge(panel, "", info.status || "unknown");

      if (info.debug_host && info.debug_port) {
        setDebug(panel, `${info.debug_host}:${info.debug_port}`);
      }
      if (info.websocket) setWS(panel, info.websocket);

      if (panels[panel].lastStatus !== info.status) {
        panels[panel].lastStatus = info.status;
        appendLog(panel, `ℹ️ status → ${info.status}`);
        if (info.error) appendLog(panel, `❌ ${info.error}`);
        if (info.websocket && st.startsWith("start")) appendLog(panel, `✅ websocket available`);
      }
    } catch (err) {
      appendLog(panel, `⚠️ poll error: ${err}`);
    }
  }

  setInterval(() => {
    pollPanel(1);
    pollPanel(2);
    pollPanel(3);
    pollPanel(4);
  }, 2000);
});
