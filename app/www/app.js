/* dyer 前端：原生 JS，无构建步骤 */
"use strict";

const state = {
  view: "jobs",
  crawlerType: "search",
  jobs: [],
  jobsTimer: null,
  logJobId: null,
  logSeq: 0,
  ws: null,
  qrTimer: null,
  videos: { page: 1, pageSize: 20, total: 0 },
  meta: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

/* ------------------------------------------------------------------ 工具 */
async function api(path, options = {}) {
  const opts = { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } };
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_) {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return null;
  return res.json();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function fmtNum(n) {
  const num = Number(n) || 0;
  if (num >= 100000000) return (num / 100000000).toFixed(1) + "亿";
  if (num >= 10000) return (num / 10000).toFixed(1) + "w";
  return String(num);
}

function fmtTime(ts) {
  if (!ts) return "-";
  const d = new Date(Number(ts) * 1000);
  if (Number.isNaN(d.getTime()) || d.getFullYear() < 2000) return "-";
  const pad = (v) => String(v).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function clock(ts) {
  const d = new Date(Number(ts) * 1000);
  const pad = (v) => String(v).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 3200);
}

const STATUS_LABEL = {
  queued: "排队中",
  running: "运行中",
  success: "已完成",
  failed: "失败",
  stopped: "已停止",
};
const TYPE_LABEL = { search: "关键词", detail: "作品", creator: "创作者" };

/* ------------------------------------------------------------------ 视图切换 */
const VIEW_META = {
  jobs: ["采集任务", "配置抖音采集任务，实时查看运行日志"],
  data: ["数据浏览", "检索已采集的作品与评论，支持导出 CSV / JSON"],
  overview: ["数据概览", "采集规模、关键词分布与高赞作品一览"],
};

function switchView(view) {
  state.view = view;
  $$(".nav-item").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
  $$(".view").forEach((section) => section.classList.add("hidden"));
  $("#view-" + view).classList.remove("hidden");
  $("#view-title").textContent = VIEW_META[view][0];
  $("#view-sub").textContent = VIEW_META[view][1];
  if (view === "data") loadVideos(1);
  if (view === "overview") loadStats();
}

/* ------------------------------------------------------------------ 引擎信息 */
async function loadMeta() {
  try {
    const meta = await api("/api/meta");
    state.meta = meta;
    const pill = $("#engine-pill");
    const engine = meta.engine;
    if (!engine.ready) {
      pill.className = "pill pill-err";
      pill.textContent = "引擎文件缺失";
    } else if (!engine.playwright) {
      pill.className = "pill pill-warn";
      pill.textContent = "缺少 Playwright";
    } else if (!engine.browsers) {
      pill.className = "pill pill-warn";
      pill.textContent = "未安装浏览器";
    } else {
      pill.className = "pill pill-ok";
      pill.textContent = "引擎就绪";
    }
    $("#data-dir").textContent = "数据目录 " + meta.data_dir;
    $("#engine-info").innerHTML = [
      ["引擎路径", engine.path],
      ["Python", engine.python],
      ["Playwright", engine.playwright ? "已安装" : "未安装"],
      ["Chromium", engine.browsers ? "已安装" : "未安装"],
      ["并发任务数", meta.max_concurrent_jobs],
      ["Cookie", meta.has_cookie ? "已保存" : "未保存"],
    ]
      .map(([k, v]) => `<div><span>${escapeHtml(k)}</span><b class="mono">${escapeHtml(v)}</b></div>`)
      .join("");
  } catch (err) {
    console.error(err);
  }
}

/* ------------------------------------------------------------------ 任务表单 */
function setCrawlerType(type) {
  state.crawlerType = type;
  $$("#crawler-type .seg").forEach((btn) => btn.classList.toggle("active", btn.dataset.type === type));
  $$(".field[data-for]").forEach((field) => field.classList.toggle("hidden", field.dataset.for !== type));
}

function splitList(text) {
  return String(text || "")
    .split(/[\n,，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function submitJob(event) {
  event.preventDefault();
  const btn = $("#btn-start");
  const payload = {
    crawler_type: state.crawlerType,
    keywords: splitList($("#keywords").value),
    aweme_ids: splitList($("#aweme-ids").value),
    creator_ids: splitList($("#creator-ids").value),
    max_notes: Number($("#max-notes").value) || 20,
    max_comments: Number($("#max-comments").value) || 0,
    start_page: Number($("#start-page").value) || 1,
    sleep_sec: Number($("#sleep-sec").value) || 0,
    enable_comments: $("#enable-comments").checked,
    enable_sub_comments: $("#enable-sub-comments").checked,
    enable_media: $("#enable-media").checked,
    headless: $("#headless").checked,
    login_type: $("#login-type").value,
    cookie: $("#cookie").value.trim(),
  };

  btn.disabled = true;
  try {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
    toast("任务已创建");
    await loadJobs();
    openLogDrawer(job.id);
    $("#cookie").value = "";
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function saveCookie() {
  const cookie = $("#cookie").value.trim();
  if (!cookie) {
    toast("请先填写 Cookie", true);
    return;
  }
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ cookie }) });
    toast("Cookie 已保存到本地");
    loadMeta();
  } catch (err) {
    toast(err.message, true);
  }
}

function syncLoginFields() {
  const isCookie = $("#login-type").value === "cookie";
  $("#cookie-field").classList.toggle("hidden", !isCookie);
  $("#headless").closest(".switch").classList.toggle("hidden", !isCookie);
  $("#form-hint").textContent = isCookie
    ? "Cookie 登录不会弹出浏览器，适合服务器部署；Cookie 失效时请重新获取"
    : "扫码登录会启动可见浏览器，请在日志抽屉中扫码完成登录";
}

/* ------------------------------------------------------------------ 任务列表 */
async function loadJobs() {
  try {
    const data = await api("/api/jobs?limit=100");
    state.jobs = data.items;
    renderJobs();
  } catch (err) {
    console.error(err);
  }
}

function jobKeywords(job) {
  const params = job.params || {};
  if (job.crawler_type === "search") return job.keywords || (params.keywords || []).join(", ");
  if (job.crawler_type === "detail") return (params.aweme_ids || []).slice(0, 2).join(", ");
  return (params.creator_ids || []).slice(0, 2).join(", ");
}

function renderJobs() {
  const list = $("#jobs-list");
  if (!state.jobs.length) {
    list.innerHTML = '<div class="empty">暂无任务，先在上方创建一个采集任务吧</div>';
    $("#jobs-summary").textContent = "";
    return;
  }
  const running = state.jobs.filter((j) => j.status === "running" || j.status === "queued").length;
  $("#jobs-summary").textContent = `共 ${state.jobs.length} 个任务 · ${running} 个进行中`;
  list.innerHTML = state.jobs
    .map((job) => {
      const status = STATUS_LABEL[job.status] || job.status;
      const canStop = job.status === "running" || job.status === "queued";
      const canRetry = ["failed", "stopped", "success"].includes(job.status);
      return `
      <div class="job-item" data-id="${job.id}">
        <div class="job-icon">${escapeHtml(TYPE_LABEL[job.crawler_type] || job.crawler_type)}</div>
        <div>
          <div class="job-title">
            <span>${escapeHtml(jobKeywords(job) || job.id)}</span>
            <span class="badge badge-${job.status}">${escapeHtml(status)}</span>
          </div>
          <div class="job-meta">
            <span class="mono">#${escapeHtml(job.id)}</span>
            <span>${escapeHtml(fmtTime(job.created_at))}</span>
            <span>${job.crawler_type === "search" ? "最多 " + (job.params.max_notes || "-") + " 个作品" : ""}</span>
          </div>
        </div>
        <div class="job-stats">
          <div><b>${job.video_count}</b>作品</div>
          <div><b>${job.comment_count}</b>评论</div>
        </div>
        <div class="job-actions">
          <button class="btn btn-ghost btn-sm" data-action="logs">日志</button>
          ${canStop ? '<button class="btn btn-danger btn-sm" data-action="stop">停止</button>' : ""}
          ${canRetry ? '<button class="btn btn-ghost btn-sm" data-action="retry">重试</button>' : ""}
          ${!canStop ? '<button class="btn btn-ghost btn-sm" data-action="delete">删除</button>' : ""}
        </div>
      </div>`;
    })
    .join("");
}

async function handleJobAction(event) {
  const btn = event.target.closest("button[data-action]");
  if (!btn) return;
  const item = event.target.closest(".job-item");
  if (!item) return;
  const jobId = item.dataset.id;
  const job = state.jobs.find((j) => j.id === jobId);
  const action = btn.dataset.action;
  event.stopPropagation();

  try {
    if (action === "logs") {
      openLogDrawer(jobId);
    } else if (action === "stop") {
      await api(`/api/jobs/${jobId}/stop`, { method: "POST" });
      toast("已请求停止任务");
      loadJobs();
    } else if (action === "retry") {
      await api(`/api/jobs/${jobId}/retry`, { method: "POST" });
      toast("已重新创建任务");
      await loadJobs();
    } else if (action === "delete") {
      if (!confirm("删除任务记录及该任务的本地数据目录？已入库的作品不会被删除。")) return;
      await api(`/api/jobs/${jobId}`, { method: "DELETE" });
      toast("任务已删除");
      loadJobs();
    }
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------------------------------------------ 日志抽屉 */
function openDrawer(title, subtitle) {
  $("#drawer-title").textContent = title;
  $("#drawer-sub").textContent = subtitle || "";
  $("#drawer").classList.remove("hidden");
  $("#drawer-mask").classList.remove("hidden");
}

function closeDrawer() {
  $("#drawer").classList.add("hidden");
  $("#drawer-mask").classList.add("hidden");
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
    state.ws = null;
  }
  clearInterval(state.qrTimer);
  state.qrTimer = null;
  state.logJobId = null;
}

function appendLog(entry) {
  const box = $("#log-box");
  if (!box) return;
  const line = document.createElement("span");
  line.className = "log-line level-" + (entry.level || "info");
  line.innerHTML = `<span class="ts">${clock(entry.ts)}</span>${escapeHtml(entry.text)}`;
  box.appendChild(line);
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  if (nearBottom) box.scrollTop = box.scrollHeight;
}

function jobById(id) {
  return state.jobs.find((job) => job.id === id);
}

async function openLogDrawer(jobId) {
  closeDrawer();
  const job = jobById(jobId) || (await api(`/api/jobs/${jobId}`));
  state.logJobId = jobId;
  state.logSeq = 0;

  const params = job.params || {};
  const isQr = params.login_type === "qrcode";
  openDrawer(
    `任务日志 · #${jobId}`,
    `${TYPE_LABEL[job.crawler_type] || job.crawler_type}采集 · ${STATUS_LABEL[job.status] || job.status}`
  );
  $("#drawer-body").innerHTML = `
    <div class="log-toolbar">
      <span class="badge badge-${job.status}" id="drawer-status">${STATUS_LABEL[job.status] || job.status}</span>
      <span class="muted small">${escapeHtml(jobKeywords(job) || "")}</span>
      <span class="grow"></span>
      <button class="btn btn-ghost btn-sm" id="btn-jump">定位最新</button>
    </div>
    <div id="qr-zone"></div>
    <div class="log-box" id="log-box"></div>`;

  $("#btn-jump").addEventListener("click", () => {
    const box = $("#log-box");
    box.scrollTop = box.scrollHeight;
  });

  if (isQr) refreshQr(jobId);

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws/jobs/${jobId}`);
  state.ws = ws;
  ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === "log") {
      if (data.seq <= state.logSeq) return;
      state.logSeq = data.seq;
      appendLog(data);
    } else if (data.type === "status") {
      const badge = $("#drawer-status");
      if (badge) {
        badge.className = "badge badge-" + data.status;
        badge.textContent = STATUS_LABEL[data.status] || data.status;
      }
      if (data.status === "success" || data.status === "failed" || data.status === "stopped") {
        clearInterval(state.qrTimer);
        loadJobs();
        if (state.view === "data") loadVideos(state.videos.page);
      }
    }
  };
  ws.onerror = () => {
    toast("日志连接失败，请稍后重试", true);
  };

  if (job.status === "running" || job.status === "queued") {
    state.qrTimer = setInterval(() => refreshQr(jobId), 2500);
  }
}

async function refreshQr(jobId) {
  if (state.logJobId !== jobId) return;
  try {
    const res = await fetch(`/api/jobs/${jobId}/qrcode?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const zone = $("#qr-zone");
    if (!zone) return;
    zone.innerHTML = `
      <div class="qr-wrap">
        <img src="${url}" alt="抖音登录二维码" />
        <div>
          <h4>请使用抖音 App 扫码登录</h4>
          <p>扫码并在手机上确认后，任务会自动继续。<br/>二维码有效期约 60 秒，过期后引擎会自动刷新。</p>
        </div>
      </div>`;
  } catch (_) {
    /* 二维码还没生成 */
  }
}

/* ------------------------------------------------------------------ 视频列表 */
async function loadVideos(page) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(state.videos.pageSize),
    sort: $("#filter-sort").value,
    order: $("#filter-order").value,
  });
  const q = $("#filter-q").value.trim();
  const keyword = $("#filter-keyword").value.trim();
  if (q) params.set("q", q);
  if (keyword) params.set("keyword", keyword);

  try {
    const data = await api("/api/videos?" + params.toString());
    state.videos.page = data.page;
    state.videos.total = data.total;
    renderVideos(data.items);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderVideos(items) {
  const body = $("#videos-body");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="7"><div class="empty">没有匹配的作品，先运行一个采集任务吧</div></td></tr>';
  } else {
    body.innerHTML = items
      .map((video) => {
        const cover = video.cover_url
          ? `<img class="cover" loading="lazy" src="${escapeHtml(video.cover_url)}" alt="" onerror="this.style.visibility='hidden'" />`
          : '<div class="cover"></div>';
        return `
        <tr data-aweme="${escapeHtml(video.aweme_id)}">
          <td>${cover}</td>
          <td>
            <div class="video-title">${escapeHtml(video.title || "(无标题)")}${video.source_keyword ? `<span class="tag">${escapeHtml(video.source_keyword)}</span>` : ""}</div>
            <div class="video-sub mono">${escapeHtml(video.aweme_id)}</div>
          </td>
          <td>${escapeHtml(video.nickname || "-")}</td>
          <td class="num">${fmtNum(video.liked_count)}</td>
          <td class="num">${fmtNum(video.comment_count)}</td>
          <td class="num">${fmtNum(video.share_count)}</td>
          <td class="small muted">${fmtTime(video.create_time)}</td>
        </tr>`;
      })
      .join("");
  }
  const totalPages = Math.max(1, Math.ceil(state.videos.total / state.videos.pageSize));
  $("#videos-total").textContent = `共 ${state.videos.total} 个作品`;
  $("#page-info").textContent = `${state.videos.page} / ${totalPages}`;
  $("#btn-prev").disabled = state.videos.page <= 1;
  $("#btn-next").disabled = state.videos.page >= totalPages;
}

function exportData(format) {
  const params = new URLSearchParams({ kind: "videos", format });
  const keyword = $("#filter-keyword").value.trim();
  if (keyword) params.set("keyword", keyword);
  window.location = "/api/export?" + params.toString();
}

/* ------------------------------------------------------------------ 作品详情 */
async function openVideoDrawer(awemeId) {
  try {
    const video = await api(`/api/videos/${awemeId}`);
    openDrawer(video.title || "作品详情", `${video.nickname || "未知作者"} · ${awemeId}`);
    $("#drawer-body").innerHTML = `
      <div class="qr-wrap" style="align-items:flex-start">
        ${video.cover_url ? `<img src="${escapeHtml(video.cover_url)}" style="width:160px;height:auto" alt="" />` : ""}
        <div style="flex:1">
          <h4 style="font-size:14px;margin-bottom:8px">${escapeHtml(video.title || "(无标题)")}</h4>
          <p style="color:var(--ink-soft);font-size:12.5px;line-height:1.8">${escapeHtml(video.desc || "")}</p>
          <div class="job-stats" style="margin-top:12px">
            <div><b>${fmtNum(video.liked_count)}</b>点赞</div>
            <div><b>${fmtNum(video.comment_count)}</b>评论</div>
            <div><b>${fmtNum(video.share_count)}</b>分享</div>
            <div><b>${fmtNum(video.collected_count)}</b>收藏</div>
          </div>
        </div>
      </div>
      <div class="log-toolbar">
        <span class="muted small">发布 ${fmtTime(video.create_time)} · 已入库评论 ${video.comment_total || 0} 条</span>
        <a class="btn btn-ghost btn-sm" href="${escapeHtml(video.aweme_url)}" target="_blank" rel="noopener">打开抖音</a>
      </div>
      <div id="comments-list"><div class="empty">加载评论…</div></div>
      <div class="pager">
        <span class="muted small" id="comments-total"></span>
        <div class="pager-actions">
          <button class="btn btn-ghost btn-sm" id="btn-comment-more">加载更多</button>
        </div>
      </div>`;
    loadComments(awemeId, 1, true);
  } catch (err) {
    toast(err.message, true);
  }
}

async function loadComments(awemeId, page, replace) {
  const box = $("#comments-list");
  if (!box) return;
  try {
    const data = await api(`/api/videos/${awemeId}/comments?page=${page}&page_size=20`);
    if (!data.items.length && replace) {
      box.innerHTML = '<div class="empty">暂无评论数据</div>';
    } else {
      const html = data.items
        .map(
          (c) => `
        <div class="job-item" style="grid-template-columns:minmax(0,1fr) auto;cursor:default">
          <div>
            <div class="job-title" style="font-weight:500">${escapeHtml(c.nickname || "匿名用户")}</div>
            <div style="font-size:13px;line-height:1.7;margin-top:4px">${escapeHtml(c.content || "")}</div>
            <div class="job-meta" style="margin-top:6px">
              <span>${fmtTime(c.create_time)}</span>
              ${c.sub_comment_count ? `<span>${c.sub_comment_count} 条回复</span>` : ""}
            </div>
          </div>
          <div style="text-align:right;color:var(--muted);font-size:12px">♥ ${fmtNum(c.like_count)}</div>
        </div>`
        )
        .join("");
      if (replace) box.innerHTML = html;
      else box.insertAdjacentHTML("beforeend", html);
    }
    $("#comments-total").textContent = `共 ${data.total} 条评论`;
    const more = $("#btn-comment-more");
    more.disabled = page * 20 >= data.total;
    more.onclick = () => loadComments(awemeId, page + 1, false);
  } catch (err) {
    box.innerHTML = `<div class="empty">${escapeHtml(err.message)}</div>`;
  }
}

/* ------------------------------------------------------------------ 数据概览 */
async function loadStats() {
  try {
    const stats = await api("/api/stats");
    $("#stat-videos").textContent = fmtNum(stats.videos);
    $("#stat-comments").textContent = fmtNum(stats.comments);
    $("#stat-liked").textContent = fmtNum(stats.liked);
    $("#stat-jobs").textContent = String(stats.jobs);

    const keywordBox = $("#keyword-bars");
    if (stats.top_keywords.length) {
      const max = Math.max(...stats.top_keywords.map((k) => k.count));
      keywordBox.innerHTML = stats.top_keywords
        .map(
          (k) => `
        <div class="bar-row">
          <span title="${escapeHtml(k.keyword)}">${escapeHtml(k.keyword)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, (k.count / max) * 100)}%"></div></div>
          <b>${k.count}</b>
        </div>`
        )
        .join("");
    } else {
      keywordBox.innerHTML = '<div class="empty">暂无数据</div>';
    }

    const dailyBox = $("#daily-bars");
    if (stats.daily.length) {
      const max = Math.max(...stats.daily.map((d) => d.count), 1);
      dailyBox.innerHTML = stats.daily
        .map(
          (d) => `
        <div class="day-col" title="${escapeHtml(d.day)}：${d.count} 个作品">
          <div class="day-bar" style="height:${Math.max(4, (d.count / max) * 100)}%"></div>
          <span class="day-label">${escapeHtml(d.day.slice(5))}</span>
        </div>`
        )
        .join("");
    } else {
      dailyBox.innerHTML = '<div class="empty">暂无数据</div>';
    }

    const hotBox = $("#hot-videos");
    if (stats.hot_videos.length) {
      hotBox.innerHTML = stats.hot_videos
        .map(
          (video, index) => `
        <div class="hot-row" data-aweme="${escapeHtml(video.aweme_id)}">
          <div class="hot-rank">${index + 1}</div>
          ${video.cover_url ? `<img class="cover" src="${escapeHtml(video.cover_url)}" alt="" onerror="this.style.visibility='hidden'" />` : '<div class="cover"></div>'}
          <div>
            <div class="video-title">${escapeHtml(video.title || "(无标题)")}</div>
            <div class="video-sub">${escapeHtml(video.nickname || "-")} · ${fmtTime(video.create_time)}</div>
          </div>
          <div class="small muted">♥ ${fmtNum(video.liked_count)}</div>
        </div>`
        )
        .join("");
    } else {
      hotBox.innerHTML = '<div class="empty">暂无数据</div>';
    }
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------------------------------------------ 设置 */
async function openSettings() {
  try {
    const settings = await api("/api/settings");
    $("#settings-cookie").value = "";
    $("#settings-cookie").placeholder = settings.has_cookie
      ? `已保存（${settings.cookie_masked}），重新输入可覆盖`
      : "douyin.com 的 Cookie 字符串";
    $("#modal-mask").classList.remove("hidden");
  } catch (err) {
    toast(err.message, true);
  }
}

async function saveSettings() {
  const cookie = $("#settings-cookie").value.trim();
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ cookie }) });
    toast("设置已保存");
    $("#modal-mask").classList.add("hidden");
    loadMeta();
  } catch (err) {
    toast(err.message, true);
  }
}

async function clearCookie() {
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ cookie: "" }) });
    toast("Cookie 已清空");
    $("#settings-cookie").value = "";
    loadMeta();
  } catch (err) {
    toast(err.message, true);
  }
}

/* ------------------------------------------------------------------ 初始化 */
function bindEvents() {
  $$(".nav-item").forEach((btn) =>
    btn.addEventListener("click", () => switchView(btn.dataset.view))
  );
  $$("#crawler-type .seg").forEach((btn) =>
    btn.addEventListener("click", () => setCrawlerType(btn.dataset.type))
  );
  $("#job-form").addEventListener("submit", submitJob);
  $("#btn-save-cookie").addEventListener("click", saveCookie);
  $("#login-type").addEventListener("change", syncLoginFields);
  $("#jobs-list").addEventListener("click", handleJobAction);
  $("#btn-refresh").addEventListener("click", () => {
    loadMeta();
    loadJobs();
    if (state.view === "data") loadVideos(state.videos.page);
    if (state.view === "overview") loadStats();
  });
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-mask").addEventListener("click", closeDrawer);

  $("#btn-search").addEventListener("click", () => loadVideos(1));
  $("#filter-q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadVideos(1);
  });
  $("#btn-prev").addEventListener("click", () => loadVideos(Math.max(1, state.videos.page - 1)));
  $("#btn-next").addEventListener("click", () => loadVideos(state.videos.page + 1));
  $("#btn-export-csv").addEventListener("click", () => exportData("csv"));
  $("#btn-export-json").addEventListener("click", () => exportData("json"));
  $("#videos-body").addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-aweme]");
    if (row) openVideoDrawer(row.dataset.aweme);
  });
  $("#hot-videos").addEventListener("click", (event) => {
    const row = event.target.closest("[data-aweme]");
    if (row) openVideoDrawer(row.dataset.aweme);
  });

  $("#btn-settings").addEventListener("click", openSettings);
  $("#modal-close").addEventListener("click", () => $("#modal-mask").classList.add("hidden"));
  $("#modal-mask").addEventListener("click", (event) => {
    if (event.target === $("#modal-mask")) $("#modal-mask").classList.add("hidden");
  });
  $("#btn-save-settings").addEventListener("click", saveSettings);
  $("#btn-clear-cookie").addEventListener("click", clearCookie);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeDrawer();
      $("#modal-mask").classList.add("hidden");
    }
  });
}

async function init() {
  bindEvents();
  syncLoginFields();
  setCrawlerType("search");
  await loadMeta();
  await loadJobs();
  state.jobsTimer = setInterval(() => {
    if (state.view === "jobs" && document.visibilityState === "visible") loadJobs();
  }, 5000);
}

document.addEventListener("DOMContentLoaded", init);
