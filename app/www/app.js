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
const TYPE_LABEL = { search: "关键词", detail: "作品", creator: "创作者", network: "扩散" };

/* ------------------------------------------------------------------ 视图切换 */
const VIEW_META = {
  jobs: ["采集任务", "配置抖音采集任务，实时查看运行日志"],
  data: ["数据浏览", "检索已采集的作品与评论，支持导出 CSV / JSON"],
  overview: ["数据概览", "采集规模、关键词分布与高赞作品一览"],
  users: ["用户数据", "以账号为单位的数据资产：资料、画像、互动关系与原始记录"],
  network: ["用户网络", "社交拓扑的聚合特征：关键节点、社区结构与用户类型分布"],
  map: ["用户地图", "按 IP 属地归纳账号分布，区分男女与未知性别"],
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
  if (view === "users") loadUserData();
  if (view === "network") loadNetwork();
  if (view === "map") loadMap();
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
  // 扩散采集可以同时使用关键词、作品、创作者作为种子
  const visible = type === "network" ? ["search", "detail", "creator"] : [type];
  $$(".field[data-for]").forEach((field) => field.classList.toggle("hidden", !visible.includes(field.dataset.for)));
  $("#network-options").classList.toggle("hidden", type !== "network");
  $("#form-hint").textContent =
    type === "network"
      ? "扩散采集会按层级展开用户与评论关系，请合理设置用户上限与请求间隔"
      : $("#login-type").value === "cookie"
        ? "Cookie 登录不会弹出浏览器，适合服务器部署；Cookie 失效时请重新获取"
        : "扫码登录会启动可见浏览器，请在日志抽屉中扫码完成登录";
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
    sleep_sec:
      state.crawlerType === "network"
        ? Number($("#network-sleep").value) || 0
        : Number($("#sleep-sec").value) || 0,
    expand_depth: Number($("#expand-depth").value) || 0,
    max_users: Number($("#max-users").value) || 50,
    max_awemes_per_user: Number($("#max-awemes-per-user").value) || 0,
    include_following: $("#include-following").checked,
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
  $("#form-hint").textContent =
    state.crawlerType === "network"
      ? "扩散采集会按层级展开用户与评论关系，请合理设置用户上限与请求间隔"
      : isCookie
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
  if (job.crawler_type === "network") {
    const seeds = [];
    if (job.keywords) seeds.push(job.keywords);
    if ((params.aweme_ids || []).length) seeds.push(`${params.aweme_ids.length} 个作品`);
    if ((params.creator_ids || []).length) seeds.push(`${params.creator_ids.length} 个创作者`);
    return seeds.join(" · ") || "扩散采集";
  }
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
          ${
            job.crawler_type === "network"
              ? `<div><b>${job.user_count}</b>用户</div><div><b>${job.relation_count}</b>关系</div>`
              : `<div><b>${job.video_count}</b>作品</div><div><b>${job.comment_count}</b>评论</div>`
          }
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


/* ------------------------------------------------------------------ 用户网络 */
const COMMUNITY_COLORS = [
  "#fe2c55", "#4f46e5", "#0891b2", "#16a34a", "#d97706",
  "#db2777", "#7c3aed", "#0ea5e9", "#65a30d", "#f97316",
];

const ROLE_COLORS = {
  hub: "#fe2c55",
  bridge: "#7c3aed",
  community_leader: "#d97706",
  active_commenter: "#0891b2",
  creator: "#16a34a",
  peripheral: "#8a8f9c",
  isolated: "#c3c6cf",
};

const ROLE_DESCRIPTIONS = [
  ["hub", "核心影响者", "入度高、PageRank 靠前，被大量账号互动"],
  ["bridge", "跨圈桥接者", "跨社区邻居占比高、介数靠前，处在结构洞位置"],
  ["community_leader", "社区核心", "所在社区内影响力最高的账号"],
  ["active_commenter", "活跃互动者", "主动评论 / 回复明显多于被动互动"],
  ["creator", "内容创作者", "有作品或被评论，构成内容供给"],
  ["peripheral", "长尾账号", "互动较少，处于网络边缘"],
  ["isolated", "孤立账号", "没有任何互动关系"],
];

const networkState = {
  users: { page: 1, pageSize: 20, total: 0 },
  analysis: null,
};

async function loadNetwork() {
  try {
    const [overview, roles] = await Promise.all([
      api("/api/network/overview"),
      api("/api/users/roles"),
    ]);
    renderNetworkOverview(overview);
    renderRoleBars(roles);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderNetworkOverview(overview) {
  const counts = overview.counts || {};
  $("#net-users").textContent = fmtNum(counts.users || 0);
  $("#net-relations").textContent = fmtNum(counts.relations || 0);
  const analysis = overview.analysis;
  networkState.analysis = analysis;
  $("#net-communities").textContent = analysis ? analysis.community_count : "-";
  $("#net-modularity").textContent = analysis ? Number(analysis.modularity || 0).toFixed(3) : "-";
  $("#community-meta").textContent = analysis
    ? `共 ${analysis.community_count} 个社区 · 模块度 ${Number(analysis.modularity || 0).toFixed(3)}`
    : "";
  renderKeyUsers();
  renderCommunities();
}

function renderKeyUsers() {
  const box = $("#key-users");
  const analysis = networkState.analysis;
  const summary = analysis && analysis.summary;
  if (!summary) {
    box.innerHTML = '<div class="empty">暂无分析结果，先运行扩散采集或点「重新分析」</div>';
    return;
  }
  const field = {
    pagerank: "top_pagerank",
    betweenness: "top_betweenness",
    degree: "top_degree",
    influence: "top_influence",
  }[$("#key-metric").value];
  const list = (summary[field] || []).slice(0, 8);
  if (!list.length) {
    box.innerHTML = '<div class="empty">暂无数据</div>';
    return;
  }
  box.innerHTML = list
    .map(
      (user, index) => `
      <div class="key-row" data-sec="${escapeHtml(user.sec_uid)}">
        <div class="hot-rank">${index + 1}</div>
        <div class="avatar-sm">${escapeHtml((user.nickname || "?").slice(0, 1))}</div>
        <div>
          <div class="video-title">${escapeHtml(user.nickname || user.sec_uid.slice(0, 10))}</div>
          <div class="video-sub">粉丝 ${fmtNum(user.follower_count)}</div>
        </div>
        <div class="small muted">${Number(user.value).toFixed(4)}</div>
      </div>`
    )
    .join("");
}

function renderCommunities() {
  const box = $("#communities");
  const analysis = networkState.analysis;
  const communities = (analysis && analysis.summary && analysis.summary.communities) || [];
  if (!communities.length) {
    box.innerHTML = '<div class="empty">暂无社区数据</div>';
    return;
  }
  const max = Math.max(...communities.map((item) => item.size));
  box.innerHTML = communities
    .slice(0, 10)
    .map((community) => {
      const leader = (community.leaders && community.leaders[0]) || {};
      return `
      <div class="bar-row">
        <span title="${escapeHtml(leader.nickname || "")}">
          <i class="dot" style="background:${COMMUNITY_COLORS[community.id % COMMUNITY_COLORS.length]}"></i>
          社区 ${community.id + 1}
        </span>
        <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, (community.size / max) * 100)}%"></div></div>
        <b>${community.size}</b>
      </div>`;
    })
    .join("");
}

/* ---------------------------------------------------------------- 用户列表 */
async function loadUsers(page) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(networkState.users.pageSize),
    sort: $("#user-sort").value,
    order: "desc",
  });
  const q = $("#user-q").value.trim();
  const depth = $("#user-depth").value;
  const role = $("#user-role").value;
  const gender = $("#user-gender").value;
  if (q) params.set("q", q);
  if (depth !== "") params.set("depth", depth);
  if (role) params.set("role", role);
  if (gender !== "") params.set("gender", gender);
  try {
    const data = await api("/api/users?" + params.toString());
    networkState.users.page = data.page;
    networkState.users.total = data.total;
    renderUsers(data.items);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderUsers(items) {
  const body = $("#users-body");
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="12"><div class="empty">没有匹配的用户，先运行一次「扩散采集」</div></td></tr>';
  } else {
    body.innerHTML = items
      .map((user) => {
        const avatar = user.avatar_url
          ? `<img class="avatar-sm" src="${escapeHtml(user.avatar_url)}" alt="" onerror="this.style.visibility='hidden'" />`
          : `<div class="avatar-sm">${escapeHtml((user.nickname || "?").slice(0, 1))}</div>`;
        const gender = user.gender === 1 ? "男" : user.gender === 2 ? "女" : "未知";
        const roleColor = ROLE_COLORS[user.role] || "#8a8f9c";
        const role = user.role_label || "未分类";
        return `
        <tr data-sec="${escapeHtml(user.sec_uid)}">
          <td>${avatar}</td>
          <td>
            <div class="video-title">${escapeHtml(user.nickname || "(无昵称)")}${
              user.is_verified ? '<span class="tag">认证</span>' : ""
            }</div>
            <div class="video-sub mono">${escapeHtml(user.unique_id || user.sec_uid.slice(0, 18))}</div>
          </td>
          <td class="num">${fmtNum(user.follower_count)}</td>
          <td class="num">${fmtNum(user.aweme_count)}</td>
          <td class="num">${fmtNum(user.total_favorited)}</td>
          <td>${escapeHtml(user.ip_location || "-")}</td>
          <td>${gender}</td>
          <td class="num">${user.depth}</td>
          <td><span class="role-badge" style="background:${roleColor}1f;color:${roleColor}">${escapeHtml(role)}</span></td>
          <td class="num">${Number(user.influence_score || 0).toFixed(1)}</td>
          <td class="num">${user.degree_total}</td>
          <td>${
            user.community >= 0
              ? `<span class="role-badge" style="background:${COMMUNITY_COLORS[user.community % COMMUNITY_COLORS.length]}1f;color:${COMMUNITY_COLORS[user.community % COMMUNITY_COLORS.length]}">社区 ${user.community + 1}</span>`
              : '<span class="muted small">-</span>'
          }</td>
        </tr>`;
      })
      .join("");
  }
  const totalPages = Math.max(1, Math.ceil(networkState.users.total / networkState.users.pageSize));
  $("#users-total").textContent = `共 ${networkState.users.total} 个用户`;
  $("#user-page-info").textContent = `${networkState.users.page} / ${totalPages}`;
  $("#btn-user-prev").disabled = networkState.users.page <= 1;
  $("#btn-user-next").disabled = networkState.users.page >= totalPages;
}

async function loadUserData() {
  await Promise.all([loadUsers(1), loadRaw()]);
}

function renderRoleBars(data) {
  const box = $("#role-bars");
  const items = (data && data.items) || [];
  $("#role-meta").textContent = data && data.total ? `共 ${data.total} 个账号` : "";
  if (!items.length) {
    box.innerHTML = '<div class="empty">暂无分析结果，先运行扩散采集或点「重新分析」</div>';
  } else {
    const max = Math.max(...items.map((item) => item.count));
    box.innerHTML = items
      .map((item) => {
        const color = ROLE_COLORS[item.role] || "#8a8f9c";
        return `
        <div class="bar-row">
          <span title="${escapeHtml(item.label)}"><i class="dot" style="background:${color}"></i>${escapeHtml(item.label)}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.max(4, (item.count / max) * 100)}%;background:${color}"></div></div>
          <b>${item.count}</b>
        </div>`;
      })
      .join("");
  }
  $("#role-desc").innerHTML = ROLE_DESCRIPTIONS.map(
    ([role, label, desc]) => `
    <div class="role-desc-row">
      <i class="dot" style="background:${ROLE_COLORS[role]}"></i>
      <b>${label}</b>
      <span>${desc}</span>
    </div>`
  ).join("");
}

async function openUserDrawer(secUid) {
  try {
    const data = await api(`/api/users/${encodeURIComponent(secUid)}`);
    const user = data.user;
    const metrics = [
      ["PageRank", Number(user.pagerank).toFixed(4)],
      ["连接度", `${user.degree_total}（入 ${user.degree_in} / 出 ${user.degree_out}）`],
      ["介数中心性", Number(user.betweenness).toFixed(3)],
      ["社区", user.community >= 0 ? `社区 ${user.community + 1}` : "-"],
    ];
    const relationRows = (list, direction) =>
      list
        .map(
          (rel) => `
        <div class="relation-row" data-sec="${escapeHtml(rel.sec_uid)}">
          <div class="avatar-sm">${escapeHtml((rel.nickname || "?").slice(0, 1))}</div>
          <div>
            <div class="video-title">${escapeHtml(rel.nickname || rel.sec_uid.slice(0, 12))}</div>
            <div class="video-sub">${direction} · ${escapeHtml(rel.rel_type)} · 权重 ${rel.weight}</div>
          </div>
          <span class="muted small">粉丝 ${fmtNum(rel.follower_count)}</span>
        </div>`
        )
        .join("") || '<div class="empty" style="padding:14px">暂无</div>';

    const videoRows =
      data.videos
        .map(
          (video) => `
        <div class="relation-row" data-video="${escapeHtml(video.aweme_id)}">
          <div class="video-sub mono">${escapeHtml(video.aweme_id)}</div>
          <div>
            <div class="video-title">${escapeHtml(video.title || "(无标题)")}</div>
            <div class="video-sub">♥ ${fmtNum(video.liked_count)} · ${fmtTime(video.create_time)}</div>
          </div>
          <a class="btn btn-ghost btn-sm" href="${escapeHtml(video.aweme_url)}" target="_blank" rel="noopener">打开</a>
        </div>`
        )
        .join("") || '<div class="empty" style="padding:14px">暂无作品</div>';

    openDrawer(user.nickname || "用户详情", `${user.unique_id || user.sec_uid}`);
    $("#drawer-body").innerHTML = `
      <div class="user-head">
        ${user.avatar_url ? `<img class="avatar-lg" src="${escapeHtml(user.avatar_url)}" alt="" />` : `<div class="avatar-lg">${escapeHtml((user.nickname || "?").slice(0, 1))}</div>`}
        <div style="flex:1">
          <h4>${escapeHtml(user.nickname || "(无昵称)")}</h4>
          <p class="muted small">${escapeHtml(user.signature || "暂无简介")}</p>
          <div class="job-stats" style="margin-top:10px">
            <div><b>${fmtNum(user.follower_count)}</b>粉丝</div>
            <div><b>${fmtNum(user.following_count)}</b>关注</div>
            <div><b>${fmtNum(user.aweme_count)}</b>作品</div>
            <div><b>${fmtNum(user.total_favorited)}</b>获赞</div>
          </div>
        </div>
      </div>
      <div class="meta-grid">
        ${metrics.map(([k, v]) => `<div><span>${k}</span><b>${escapeHtml(v)}</b></div>`).join("")}
        <div><span>发现层级</span><b>${user.depth}</b></div>
        <div><span>来源</span><b>${escapeHtml(user.source)}</b></div>
        <div><span>IP 属地</span><b>${escapeHtml(user.ip_location || "-")}</b></div>
        <div><span>资料完整</span><b>${user.profile_complete ? "是" : "否"}</b></div>
      </div>
      <div class="drawer-actions">
        <a class="btn btn-ghost btn-sm" href="${escapeHtml(user.home_url)}" target="_blank" rel="noopener">打开主页</a>
        <span class="muted small">已入库评论 ${data.comment_total} 条</span>
      </div>
      <h4 class="section-title">出边（该用户主动互动）</h4>
      ${relationRows(data.relations.out, "→")}
      <h4 class="section-title">入边（他人与该用户互动）</h4>
      ${relationRows(data.relations.in, "←")}
      <h4 class="section-title">该用户的作品</h4>
      ${videoRows}`;

    $("#drawer-body").querySelectorAll(".relation-row[data-sec]").forEach((row) => {
      row.addEventListener("click", () => openUserDrawer(row.dataset.sec));
    });
  } catch (err) {
    toast(err.message, true);
  }
}

/* ---------------------------------------------------------------- 原始数据 */
async function loadRaw() {
  try {
    const data = await api("/api/raw?page_size=8");
    $("#raw-meta").textContent = `共 ${data.total} 条原始响应`;
    if (!data.items.length) {
      $("#raw-list").innerHTML = '<div class="empty">暂无原始数据</div>';
      return;
    }
    $("#raw-list").innerHTML = data.items
      .map((item) => {
        let pretty = item.payload;
        try {
          pretty = JSON.stringify(JSON.parse(item.payload), null, 2);
        } catch (_) {
          /* 保持原文 */
        }
        return `
        <details class="raw-item">
          <summary>
            <span class="badge">${escapeHtml(item.kind)}</span>
            <span class="mono small">${escapeHtml(item.external_id || "-")}</span>
            <span class="muted small">${fmtTime(item.fetched_at)} · 深度 ${item.depth} · ${escapeHtml(item.source)}</span>
          </summary>
          <pre>${escapeHtml(pretty)}</pre>
        </details>`;
      })
      .join("");
  } catch (err) {
    toast(err.message, true);
  }
}

async function analyzeNetwork() {
  const button = $("#btn-analyze");
  button.disabled = true;
  try {
    await api("/api/network/analyze", { method: "POST" });
    toast("社交网络分析已更新");
    await loadNetwork();
  } catch (err) {
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
}


/* ------------------------------------------------------------------ 用户地图 */
const MAP_BUCKETS = [
  { max: 0, color: "#eceef3", label: "0" },
  { max: 2, color: "#ffc9d5", label: "1–2" },
  { max: 5, color: "#ff93a9", label: "3–5" },
  { max: 10, color: "#fb5b7d", label: "6–10" },
  { max: Infinity, color: "#d81e46", label: "11+" },
];

const MAP_SORT_LABEL = { total: "账号", male: "男", female: "女" };

const mapState = { geo: null, data: null, selected: "" };

function mapColor(total) {
  const bucket = MAP_BUCKETS.find((item) => total <= item.max) || MAP_BUCKETS[MAP_BUCKETS.length - 1];
  return bucket.color;
}

async function loadMap() {
  try {
    const [data, geo] = await Promise.all([api("/api/users/map"), loadChinaGeo()]);
    mapState.data = data;
    renderMapStats(data);
    drawChinaMap(geo, data);
    renderRegionList(data);
  } catch (err) {
    toast(err.message, true);
  }
}

async function loadChinaGeo() {
  if (mapState.geo) return mapState.geo;
  const response = await fetch("/static/geo/china.json", { cache: "force-cache" });
  if (!response.ok) throw new Error("地图数据加载失败");
  mapState.geo = await response.json();
  return mapState.geo;
}

function renderMapStats(data) {
  const totals = data.totals || { total: 0, male: 0, female: 0, unknown: 0 };
  $("#map-regions").textContent = (data.known || []).length;
  $("#map-total").textContent = fmtNum(totals.total);
  $("#map-male").textContent = fmtNum(totals.male);
  $("#map-female").textContent = fmtNum(totals.female);
  $("#map-meta").textContent = `共 ${fmtNum(totals.total)} 个账号 · 未知性别 ${fmtNum(totals.unknown)}`;
}

function buildProjection(geo, width, height) {
  let minLon = Infinity;
  let maxLon = -Infinity;
  let minLat = Infinity;
  let maxLat = -Infinity;
  const visit = (coords) => {
    if (typeof coords[0] === "number") {
      const [lon, lat] = coords;
      minLon = Math.min(minLon, lon);
      maxLon = Math.max(maxLon, lon);
      minLat = Math.min(minLat, lat);
      maxLat = Math.max(maxLat, lat);
      return;
    }
    coords.forEach(visit);
  };
  geo.features.forEach((feature) => visit(feature.geometry.coordinates));
  const pad = 8;
  const scale = Math.min(
    (width - pad * 2) / (maxLon - minLon),
    (height - pad * 2) / (maxLat - minLat)
  );
  const offsetX = (width - (maxLon - minLon) * scale) / 2;
  const offsetY = (height - (maxLat - minLat) * scale) / 2;
  return ([lon, lat]) => [offsetX + (lon - minLon) * scale, offsetY + (maxLat - lat) * scale];
}

function simplifyRing(points, tolerance) {
  if (points.length <= 3) return points;
  const out = [points[0]];
  for (let i = 1; i < points.length - 1; i += 1) {
    const prev = out[out.length - 1];
    if (Math.hypot(points[i][0] - prev[0], points[i][1] - prev[1]) >= tolerance) {
      out.push(points[i]);
    }
  }
  out.push(points[points.length - 1]);
  return out;
}

function featureToPath(feature, project) {
  const polygons =
    feature.geometry.type === "Polygon" ? [feature.geometry.coordinates] : feature.geometry.coordinates;
  let d = "";
  for (const polygon of polygons) {
    for (const ring of polygon) {
      const simplified = simplifyRing(ring.map(project), 0.9);
      if (simplified.length < 3) continue;
      d +=
        "M" +
        simplified.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join("L") +
        "Z";
    }
  }
  return d;
}

function drawChinaMap(geo, data) {
  const svg = $("#china-map");
  const empty = $("#map-empty");
  const totals = (data && data.totals) || { total: 0 };
  if (!totals.total) {
    svg.innerHTML = "";
    empty.classList.remove("hidden");
    $("#map-legend").innerHTML = "";
    return;
  }
  empty.classList.add("hidden");
  const byRegion = new Map((data.regions || []).map((item) => [item.region, item]));
  const project = buildProjection(geo, 820, 640);
  const paths = [];
  geo.features.forEach((feature) => {
    const name = feature.properties && feature.properties.name;
    if (!name) return;
    const item = byRegion.get(name);
    const total = item ? item.total : 0;
    const d = featureToPath(feature, project);
    if (!d) return;
    paths.push(
      `<path data-region="${escapeHtml(name)}" d="${d}" fill="${mapColor(total)}" stroke="#ffffff" stroke-width="0.7"></path>`
    );
  });
  svg.innerHTML = paths.join("");

  svg.querySelectorAll("path[data-region]").forEach((path) => {
    const name = path.dataset.region;
    path.addEventListener("mouseenter", (event) => showMapTip(event, name));
    path.addEventListener("mousemove", moveMapTip);
    path.addEventListener("mouseleave", hideMapTip);
    path.addEventListener("click", () => selectMapRegion(name));
  });

  $("#map-legend").innerHTML = MAP_BUCKETS.map(
    (bucket) => `<span class="legend-item"><i class="dot" style="background:${bucket.color}"></i>${bucket.label}</span>`
  ).join("");
}

function showMapTip(event, region) {
  const tip = $("#map-tip");
  const item = ((mapState.data && mapState.data.regions) || []).find((row) => row.region === region);
  tip.innerHTML = item
    ? `<strong>${escapeHtml(region)}</strong>
       <div>账号 ${fmtNum(item.total)} · 男 ${fmtNum(item.male)} · 女 ${fmtNum(item.female)}</div>
       <div>性别未知 ${fmtNum(item.unknown)}</div>`
    : `<strong>${escapeHtml(region)}</strong><div>暂无账号</div>`;
  tip.classList.remove("hidden");
  moveMapTip(event);
}

function moveMapTip(event) {
  const tip = $("#map-tip");
  const wrap = tip.parentElement.getBoundingClientRect();
  const x = event.clientX - wrap.left;
  const y = event.clientY - wrap.top;
  tip.style.left = `${Math.min(Math.max(x + 14, 8), wrap.width - 190)}px`;
  tip.style.top = `${Math.max(y - 12, 8)}px`;
}

function hideMapTip() {
  $("#map-tip").classList.add("hidden");
}

function selectMapRegion(region) {
  mapState.selected = region;
  document.querySelectorAll(".region-row[data-region]").forEach((row) => {
    row.classList.toggle("active", row.dataset.region === region);
  });
  const row = document.querySelector(`.region-row[data-region="${CSS.escape(region)}"]`);
  if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function regionRow(item, sort, max) {
  const value = Number(item[sort] || 0);
  return `
    <div class="bar-row region-row" data-region="${escapeHtml(item.region)}">
      <span title="${escapeHtml(item.region)}">${escapeHtml(item.region)}</span>
      <div class="bar-track">
        <div class="bar-fill" style="width:${Math.max(3, (value / Math.max(max, 1)) * 100)}%"></div>
      </div>
      <b>${MAP_SORT_LABEL[sort] || "账号"} ${fmtNum(value)}
        <small class="muted">总数 ${fmtNum(item.total)} · ♂${fmtNum(item.male)} ♀${fmtNum(item.female)}</small>
      </b>
    </div>`;
}

function renderRegionList(data) {
  const sort = $("#map-sort").value;
  const known = [...(data.known || [])].sort(
    (a, b) => b[sort] - a[sort] || b.total - a.total
  );
  const max = Math.max(...known.map((item) => Number(item[sort] || 0)), 1);
  $("#region-list").innerHTML = known.length
    ? known.map((item) => regionRow(item, sort, max)).join("")
    : '<div class="empty">暂无数据</div>';

  const others = [...(data.others || [])].sort((a, b) => b.total - a.total);
  const otherMax = Math.max(...others.map((item) => item.total), 1);
  $("#region-others").innerHTML = others.length
    ? others.map((item) => regionRow(item, "total", otherMax)).join("")
    : '<div class="empty">暂无数据</div>';

  document.querySelectorAll(".region-row[data-region]").forEach((row) => {
    row.classList.toggle("active", row.dataset.region === mapState.selected);
    row.addEventListener("click", () => {
      const name = row.dataset.region;
      selectMapRegion(name);
      const item = (data.regions || []).find((entry) => entry.region === name);
      if (item) toast(`${name}：账号 ${item.total} · 男 ${item.male} · 女 ${item.female}`);
    });
  });
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

  $("#key-metric").addEventListener("change", renderKeyUsers);
  $("#map-sort").addEventListener("change", () => {
    if (mapState.data) renderRegionList(mapState.data);
  });
  $("#btn-user-search").addEventListener("click", () => loadUsers(1));
  $("#user-q").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadUsers(1);
  });
  $("#user-sort").addEventListener("change", () => loadUsers(1));
  $("#user-depth").addEventListener("change", () => loadUsers(1));
  $("#user-role").addEventListener("change", () => loadUsers(1));
  $("#user-gender").addEventListener("change", () => loadUsers(1));
  $("#btn-user-prev").addEventListener("click", () => loadUsers(Math.max(1, networkState.users.page - 1)));
  $("#btn-user-next").addEventListener("click", () => loadUsers(networkState.users.page + 1));
  $("#btn-analyze").addEventListener("click", analyzeNetwork);
  $("#users-body").addEventListener("click", (event) => {
    const row = event.target.closest("tr[data-sec]");
    if (row) openUserDrawer(row.dataset.sec);
  });
  $("#key-users").addEventListener("click", (event) => {
    const row = event.target.closest("[data-sec]");
    if (row) openUserDrawer(row.dataset.sec);
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
