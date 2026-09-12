/* mdf2sql - lop giao dien. Goi API cuc bo, khong gui gi ra ngoai internet. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { jobId: null, timer: null, report: null };

/* ---------- tien ich ---------- */

// Mã thông hành lấy từ địa chỉ trang, để trang web khác trên máy không gọi được API.
const TOKEN = new URLSearchParams(location.search).get("k") || "";

async function api(path, body) {
  const headers = { "X-Token": TOKEN };
  if (body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method: body ? "POST" : "GET",
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: "Máy chủ trả về dữ liệu không đọc được." }));
  if (!res.ok) throw new Error(data.error || "Lỗi không xác định");
  return data;
}

const nf = new Intl.NumberFormat("vi-VN");

function humanSize(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0, n = bytes;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(n < 10 ? 2 : 1)) + " " + units[i];
}

function clock() {
  return new Date().toLocaleTimeString("vi-VN", { hour12: false });
}

function show(el, visible) { el.hidden = !visible; }

/* ---------- giao dien sang toi ---------- */

const THEME_KEY = "mdf2sql.theme";
function applyTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  try { localStorage.setItem(THEME_KEY, mode); } catch (_) {}
}
try {
  const saved = localStorage.getItem(THEME_KEY);
  if (saved) document.documentElement.setAttribute("data-theme", saved);
} catch (_) {}
$("themeToggle").addEventListener("click", () => {
  const now = document.documentElement.getAttribute("data-theme");
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const current = now === "auto" ? (dark ? "dark" : "light") : now;
  applyTheme(current === "dark" ? "light" : "dark");
});

/* ---------- dò SQL Server ---------- */

async function loadServers() {
  const badge = $("serverStatus");
  try {
    const data = await api("/api/instances");
    if (!data.instances.length) {
      badge.textContent = "Chưa có SQL Server trên máy";
      badge.className = "pill pill--danger";
      $("runHint").textContent =
        "Cần cài SQL Server Express hoặc LocalDB (miễn phí) rồi mở lại tool.";
      return;
    }
    badge.textContent = data.instances[0].label;
    badge.className = "pill pill--ok";
    badge.title = "Đang dùng: " + data.instances[0].server;
  } catch (err) {
    badge.textContent = "Không kết nối được SQL Server";
    badge.className = "pill pill--danger";
    badge.title = err.message;
  }
}

/* ---------- chọn file ---------- */

async function inspect(path) {
  const card = $("fileCard");
  if (!path.trim()) { show(card, false); setReady(false); return; }
  try {
    const info = await api("/api/inspect", { path });
    $("fileName").textContent = info.name;
    $("fileName").title = info.path;
    $("fileDb").textContent = info.db_name || "không đọc được";
    $("fileVer").textContent = info.server_version || ("version " + info.internal_version);
    $("fileSize").textContent = humanSize(info.size);
    $("fileLog").textContent = info.has_ldf ? "có, sẽ dùng kèm" : "không có, sẽ dựng lại";

    const st = $("fileState");
    if (info.has_ldf) {
      st.textContent = "Đầy đủ"; st.className = "pill pill--ok";
    } else {
      st.textContent = "Thiếu file log"; st.className = "pill pill--warn";
    }
    show(card, true);

    if (!$("outPath").value.trim()) $("outPath").value = info.suggest_out;
    if (!$("targetDb").value.trim()) $("targetDb").placeholder = info.db_name || "Giữ nguyên tên gốc";
    setReady(true);
  } catch (err) {
    show(card, false);
    setReady(false);
    $("runHint").textContent = err.message;
  }
}

function setReady(ok) {
  $("runBtn").disabled = !ok;
  if (ok) $("runHint").textContent = "File gốc của bạn không bị thay đổi.";
}

$("pickMdf").addEventListener("click", async () => {
  try {
    const r = await api("/api/pick", { kind: "mdf" });
    if (r.path) { $("mdfPath").value = r.path; $("outPath").value = ""; inspect(r.path); }
  } catch (err) { $("runHint").textContent = err.message; }
});

$("pickOut").addEventListener("click", async () => {
  try {
    const r = await api("/api/pick", { kind: "sql", current: $("outPath").value });
    if (r.path) $("outPath").value = r.path;
  } catch (err) { $("runHint").textContent = err.message; }
});

let typeTimer = null;
$("mdfPath").addEventListener("input", (e) => {
  clearTimeout(typeTimer);
  const v = e.target.value;
  typeTimer = setTimeout(() => inspect(v), 400);
});

/* ---------- chạy chuyển đổi ---------- */

$("runBtn").addEventListener("click", async () => {
  const payload = {
    path: $("mdfPath").value.trim(),
    out: $("outPath").value.trim(),
    target_db: $("targetDb").value.trim(),
    include_data: $("optData").checked,
    create_database: $("optCreateDb").checked,
    drop_if_exists: $("optDrop").checked,
    auto_repair: $("optRepair").checked,
    skip_blobs: $("optSkipBlob").checked,
  };
  show($("emptyState"), false);
  show($("resultBox"), false);
  show($("errorBox"), false);
  show($("progressBox"), true);
  $("log").innerHTML = "";
  setRunning(true);

  try {
    const r = await api("/api/convert", payload);
    state.jobId = r.job_id;
    state.timer = setInterval(poll, 400);
  } catch (err) {
    fail(err.message);
  }
});

function setRunning(on) {
  $("runBtn").disabled = on;
  $("runBtn").textContent = on ? "Đang chuyển đổi..." : "Chuyển đổi";
  document.querySelectorAll(".panel input, .panel .btn--outline")
    .forEach((el) => { el.disabled = on; });
  $("runBtn").disabled = on;
}

let lastLogged = "";
function poll() {
  api("/api/progress?id=" + encodeURIComponent(state.jobId))
    .then((job) => {
      $("progressStage").textContent = job.message || "Đang xử lý";
      $("progressPct").textContent = job.percent + "%";
      $("progressFill").style.width = job.percent + "%";
      $("progressBar").setAttribute("aria-valuenow", String(job.percent));

      if (job.message && job.message !== lastLogged) {
        lastLogged = job.message;
        const li = document.createElement("li");
        li.setAttribute("data-t", clock());
        li.append(document.createTextNode(job.message));
        $("log").append(li);
        $("log").scrollTop = $("log").scrollHeight;
      }

      if (job.done) {
        clearInterval(state.timer);
        setRunning(false);
        if (job.error) fail(job.error);
        else succeed(job.report);
      }
    })
    .catch((err) => { clearInterval(state.timer); setRunning(false); fail(err.message); });
}

function fail(message) {
  show($("progressBox"), false);
  show($("errorBox"), true);
  $("errorText").textContent = message;
}

function succeed(rep) {
  state.report = rep;
  $("stTables").textContent = nf.format(rep.tables);
  $("stRows").textContent = nf.format(rep.rows);
  $("stSize").textContent = humanSize(rep.bytes_written);
  $("stTime").textContent = rep.seconds.toFixed(1);
  $("outFilePath").textContent = rep.out_path;
  $("cmdLine").textContent =
    'sqlcmd -S .\\SQLEXPRESS -E -f 65001 -i "' + rep.out_path + '"';

  const warnBox = $("warnBox"), warnList = $("warnList");
  warnList.innerHTML = "";
  if (rep.warnings && rep.warnings.length) {
    rep.warnings.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      warnList.append(li);
    });
    show(warnBox, true);
  } else {
    show(warnBox, false);
  }

  const detailBox = $("detailBox"), detailList = $("detailList");
  detailList.innerHTML = "";
  if (rep.details && rep.details.length) {
    rep.details.forEach((d) => {
      const li = document.createElement("li");
      li.textContent = d;
      detailList.append(li);
    });
    show(detailBox, true);
  } else {
    show(detailBox, false);
  }

  const body = $("tableRows");
  body.innerHTML = "";
  (rep.table_stats || []).forEach((t) => {
    const tr = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = t.table;
    const rows = document.createElement("td");
    rows.className = "num";
    rows.textContent = nf.format(t.rows);
    const st = document.createElement("td");
    const pill = document.createElement("span");
    if (!t.complete) {
      pill.className = "pill pill--danger";
      pill.textContent = "đọc thiếu";
    } else if (t.rejected) {
      pill.className = "pill pill--warn";
      pill.textContent = "bỏ " + nf.format(t.rejected) + " dòng lỗi";
    } else {
      pill.className = "pill pill--ok";
      pill.textContent = "đủ";
    }
    st.append(pill);
    tr.append(name, rows, st);
    body.append(tr);
  });

  show($("progressBox"), true);
  show($("resultBox"), true);
}

$("openFolder").addEventListener("click", () => {
  api("/api/reveal", { path: state.report.out_path }).catch(() => {});
});

$("copyPath").addEventListener("click", async (e) => {
  try {
    await navigator.clipboard.writeText(state.report.out_path);
    e.target.textContent = "Đã chép";
    setTimeout(() => { e.target.textContent = "Chép đường dẫn"; }, 1600);
  } catch (_) {}
});

loadServers();
