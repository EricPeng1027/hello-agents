/* ReportWriter Web UI 前端状态机
 *
 * 状态流转：idle → writing（阶段A：导入+规划）→ outline_review（确认大纲）
 *          → drafting（阶段B：撰写+评审）→ done ↔ revising / error
 * 进度经 SSE（/api/events/{sid}）推送；EventSource 原生自动重连，
 * 服务端在重连时回放 backlog，客户端用 seq 去重。
 */

const S = {
  IDLE: "idle",
  WRITING: "writing",
  OUTLINE: "outline_review",
  DRAFTING: "drafting",
  DONE: "done",
  REVISING: "revising",
  ERROR: "error",
};

const STATUS_LABEL = {
  idle: "空闲",
  writing: "规划中",
  outline_review: "待确认大纲",
  drafting: "撰写中",
  done: "已完成",
  revising: "修订中",
  error: "出错",
};

let state = S.IDLE;
let sessionId = null;
let es = null;
let lastSeq = 0;
let typesCache = [];

// ------------------------------------------------------------ DOM 快捷方式
const $ = (id) => document.getElementById(id);
const els = {
  typeSelect: $("typeSelect"),
  typePreview: $("typePreview"),
  topicInput: $("topicInput"),
  uploadScope: $("uploadScope"),
  fileInput: $("fileInput"),
  btnUpload: $("btnUpload"),
  uploadList: $("uploadList"),
  btnStart: $("btnStart"),
  statusBadge: $("statusBadge"),
  progressBar: $("progressBarInner"),
  progressLog: $("progressLog"),
  cardOutline: $("card-outline"),
  outlineTitle: $("outlineTitle"),
  outlineSections: $("outlineSections"),
  btnConfirmOutline: $("btnConfirmOutline"),
  cardDraft: $("card-draft"),
  draftTitle: $("draftTitle"),
  draftWords: $("draftWords"),
  reviseRounds: $("reviseRounds"),
  dlMarkdown: $("dlMarkdown"),
  dlDocx: $("dlDocx"),
  sectionsView: $("sectionsView"),
  globalFeedback: $("globalFeedback"),
  btnRevise: $("btnRevise"),
  cardWelcome: $("card-welcome"),
};

// ------------------------------------------------------------ 状态切换
function setState(next, errMsg) {
  state = next;
  els.statusBadge.textContent = STATUS_LABEL[next] || next;
  els.statusBadge.dataset.state = next;

  const busy = next === S.WRITING || next === S.DRAFTING || next === S.REVISING;
  els.btnStart.disabled = busy;
  els.btnConfirmOutline.disabled = next !== S.OUTLINE;
  els.btnRevise.disabled = next !== S.DONE;

  els.cardOutline.classList.toggle("hidden", next !== S.OUTLINE);
  els.cardDraft.classList.toggle(
    "hidden",
    !(next === S.DONE || next === S.REVISING)
  );
  els.cardWelcome.classList.toggle("hidden", next !== S.IDLE && next !== S.ERROR);

  if (next === S.ERROR && errMsg) appendLog({ stage: "error", message: errMsg });
}

// ------------------------------------------------------------ 进度展示
function appendLog(ev) {
  const li = document.createElement("li");
  li.textContent = `[${ev.stage}] ${ev.message}`;
  if (ev.stage === "error") li.classList.add("log-error");
  els.progressLog.appendChild(li);
  els.progressLog.scrollTop = els.progressLog.scrollHeight;
  if (ev.progress && ev.progress.total) {
    const pct = Math.round((100 * ev.progress.current) / ev.progress.total);
    els.progressBar.style.width = pct + "%";
  }
}

function resetProgress() {
  els.progressLog.innerHTML = "";
  els.progressBar.style.width = "0%";
  lastSeq = 0;
}

// ------------------------------------------------------------ API 调用
async function api(path, options = {}) {
  const resp = await fetch(`/api${path}`, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return resp.json();
}

async function loadTypes() {
  typesCache = await api("/types");
  els.typeSelect.innerHTML = typesCache
    .map((t) => `<option value="${t.type_id}">${t.name}</option>`)
    .join("");
  renderTypePreview();
}

function renderTypePreview() {
  const t = typesCache.find((x) => x.type_id === els.typeSelect.value);
  if (!t) {
    els.typePreview.innerHTML = "";
    return;
  }
  els.typePreview.innerHTML =
    "<ul>" +
    t.sections
      .map((s) => `<li>${s.title}（约 ${s.target_words} 字）</li>`)
      .join("") +
    "</ul>";
}

async function uploadFiles() {
  const files = els.fileInput.files;
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("scope", els.uploadScope.value);
  els.btnUpload.disabled = true;
  try {
    const res = await api("/materials/upload", { method: "POST", body: fd });
    for (const name of res.saved) {
      const li = document.createElement("li");
      li.textContent = `✅ ${name}`;
      els.uploadList.appendChild(li);
    }
    for (const r of res.rejected) {
      const li = document.createElement("li");
      li.textContent = `❌ ${r.name}：${r.reason}`;
      li.classList.add("log-error");
      els.uploadList.appendChild(li);
    }
    els.fileInput.value = "";
  } catch (e) {
    alert("上传失败：" + e.message);
  } finally {
    els.btnUpload.disabled = false;
  }
}

// ------------------------------------------------------------ 撰写流程
async function startWrite() {
  const topic = els.topicInput.value.trim();
  if (!topic) {
    alert("请先填写主题");
    return;
  }
  resetProgress();
  try {
    const res = await api("/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type_id: els.typeSelect.value, topic }),
    });
    sessionId = res.session_id;
    setState(S.WRITING);
    openEvents();
  } catch (e) {
    setState(S.ERROR, e.message);
  }
}

function openEvents() {
  if (es) es.close();
  es = new EventSource(`/api/events/${sessionId}`);
  es.onmessage = (msg) => {
    // 心跳注释不会触发 onmessage
    let ev;
    try {
      ev = JSON.parse(msg.data);
    } catch (_) {
      return;
    }
    if (ev.seq && ev.seq <= lastSeq) return; // 重连回放去重
    if (ev.seq) lastSeq = ev.seq;
    appendLog(ev);
    if (ev.stage === "outline_ready") {
      fetchOutline();
    } else if (ev.stage === "done") {
      fetchDraft();
    } else if (ev.stage === "error") {
      setState(S.ERROR, ev.message);
    }
  };
  es.onerror = () => {
    // 终态后断开不再重连
    if (state === S.DONE || state === S.ERROR) es.close();
  };
}

// ------------------------------------------------------------ 大纲确认
async function fetchOutline() {
  try {
    const outline = await api(`/outline/${sessionId}`);
    renderOutlineEditor(outline);
    setState(S.OUTLINE);
  } catch (e) {
    setState(S.ERROR, e.message);
  }
}

function renderOutlineEditor(outline) {
  els.outlineTitle.value = outline.title || "";
  els.outlineSections.innerHTML = "";
  (outline.sections || []).forEach((sec) => {
    const div = document.createElement("div");
    div.className = "outline-sec";
    div.dataset.key = sec.key;
    div.innerHTML = `
      <div class="outline-sec-head">
        <input class="sec-title" type="text" value="${escapeAttr(sec.title || sec.key)}">
        <span class="sec-key">${sec.key}</span>
      </div>
      <textarea class="sec-summary" rows="2" placeholder="本章概述">${escapeHtml(sec.summary || "")}</textarea>
      <textarea class="sec-points" rows="3" placeholder="要点（一行一个）">${escapeHtml((sec.key_points || []).join("\n"))}</textarea>
    `;
    els.outlineSections.appendChild(div);
  });
}

async function confirmOutline() {
  const sections = [];
  document.querySelectorAll(".outline-sec").forEach((div) => {
    sections.push({
      key: div.dataset.key,
      title: div.querySelector(".sec-title").value.trim(),
      summary: div.querySelector(".sec-summary").value.trim(),
      key_points: div
        .querySelector(".sec-points")
        .value.split("\n")
        .map((s) => s.trim())
        .filter(Boolean),
    });
  });
  try {
    await api(`/outline/${sessionId}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: els.outlineTitle.value.trim(), sections }),
    });
    setState(S.DRAFTING);
  } catch (e) {
    alert("确认大纲失败：" + e.message);
  }
}

// ------------------------------------------------------------ 成稿与反馈
async function fetchDraft() {
  try {
    const draft = await api(`/draft/${sessionId}`);
    renderDraft(draft);
    setState(S.DONE);
  } catch (e) {
    setState(S.ERROR, e.message);
  }
}

function renderDraft(draft) {
  els.draftTitle.textContent = draft.title;
  els.draftWords.textContent = `总字数 ${draft.total_words}`;
  els.reviseRounds.textContent =
    draft.revision_rounds > 0 ? `第 ${draft.revision_rounds} 轮修订` : "";
  els.dlMarkdown.href = `/api/download/${sessionId}/markdown`;
  els.dlDocx.href = `/api/download/${sessionId}/docx`;

  els.sectionsView.innerHTML = "";
  draft.sections.forEach((sec) => {
    const card = document.createElement("div");
    card.className = "sec-card";
    card.innerHTML = `
      <div class="sec-head">
        <h3>${escapeHtml(sec.title)}</h3>
        <span class="word-count">${sec.word_count} 字${sec.user_revised ? " · 已修订" : ""}</span>
      </div>
      <div class="md-body">${renderMarkdown(sec.content || "")}</div>
      <details class="raw-md"><summary>查看原始 Markdown</summary><pre>${escapeHtml(sec.content || "")}</pre></details>
      <textarea class="sec-feedback" data-key="${escapeAttr(sec.key)}" rows="2"
                placeholder="对本节的修改意见（留空则本节不改）"></textarea>
    `;
    els.sectionsView.appendChild(card);
  });
  els.globalFeedback.value = "";
}

function collectFeedback() {
  const sf = {};
  document.querySelectorAll(".sec-feedback").forEach((t) => {
    if (t.value.trim()) sf[t.dataset.key] = t.value.trim();
  });
  return {
    global_feedback: els.globalFeedback.value.trim(),
    section_feedback: sf,
  };
}

async function submitRevise() {
  const fb = collectFeedback();
  if (!fb.global_feedback && !Object.keys(fb.section_feedback).length) {
    alert("请先填写修改意见（全局或至少一节）");
    return;
  }
  try {
    await api(`/revise/${sessionId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fb),
    });
    setState(S.REVISING);
  } catch (e) {
    alert("提交修订失败：" + e.message);
  }
}

// ------------------------------------------------------------ 工具
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/"/g, "&quot;");
}

// ------------------------------------------------------------ 启动
els.typeSelect.addEventListener("change", renderTypePreview);
els.btnUpload.addEventListener("click", uploadFiles);
els.btnStart.addEventListener("click", startWrite);
els.btnConfirmOutline.addEventListener("click", confirmOutline);
els.btnRevise.addEventListener("click", submitRevise);

loadTypes().catch((e) => appendLog({ stage: "error", message: "类型加载失败: " + e.message }));
setState(S.IDLE);
