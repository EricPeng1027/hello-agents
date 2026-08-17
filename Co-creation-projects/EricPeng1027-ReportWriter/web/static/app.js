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
  CLARIFYING: "clarifying",
  CHOOSING: "choosing",
  ERROR: "error",
};

const STATUS_LABEL = {
  idle: "空闲",
  writing: "规划中",
  outline_review: "待确认大纲",
  drafting: "撰写中",
  done: "已完成",
  revising: "修订中",
  clarifying: "对话澄清中",
  choosing: "待确认生成",
  error: "出错",
};

let state = S.IDLE;
let sessionId = null;
let es = null;
let lastSeq = 0;
let typesCache = [];
let currentView = "write"; // write | materials

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
  cardChat: $("card-chat"),
  chatMessages: $("chatMessages"),
  chatInput: $("chatInput"),
  btnChatStart: $("btnChatStart"),
  btnChatSend: $("btnChatSend"),
  btnChatGenerate: $("btnChatGenerate"),
  materialTypeFilter: $("materialTypeFilter"),
  matUploadType: $("matUploadType"),
  materialsList: $("materialsList"),
  materialsMsg: $("materialsMsg"),
  btnReingest: $("btnReingest"),
  navBtns: document.querySelectorAll(".nav-btn"),
  viewWrite: $("view-write"),
  viewMaterials: $("view-materials"),
};

// ------------------------------------------------------------ 视图切换（导航）
function switchView(view) {
  currentView = view;
  els.navBtns.forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  els.viewWrite.classList.toggle("hidden", view !== "write");
  els.viewMaterials.classList.toggle("hidden", view !== "materials");
  if (view === "materials") loadMaterials();
}

// ------------------------------------------------------------ 状态切换
function setState(next, errMsg) {
  state = next;
  els.statusBadge.textContent = STATUS_LABEL[next] || next;
  els.statusBadge.dataset.state = next;

  const busy = next === S.WRITING || next === S.DRAFTING || next === S.REVISING;
  const chatting = next === S.CLARIFYING || next === S.CHOOSING;
  els.btnStart.disabled = busy || chatting;
  els.btnChatStart.disabled = busy || chatting;
  els.btnConfirmOutline.disabled = next !== S.OUTLINE;
  els.btnRevise.disabled = next !== S.DONE;
  els.btnChatSend.disabled = !chatting;
  els.btnChatGenerate.disabled = !chatting;

  els.cardOutline.classList.toggle("hidden", next !== S.OUTLINE);
  els.cardDraft.classList.toggle(
    "hidden",
    !(next === S.DONE || next === S.REVISING)
  );
  els.cardChat.classList.toggle("hidden", !chatting);
  els.cardWelcome.classList.toggle(
    "hidden",
    !(next === S.IDLE || next === S.ERROR)
  );

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
  const options = typesCache
    .map((t) => `<option value="${t.type_id}">${t.name}</option>`)
    .join("");
  els.typeSelect.innerHTML = options;
  els.materialTypeFilter.innerHTML = `<option value="">全部类型</option>` + options;
  els.matUploadType.innerHTML = options;
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
  // 上传目标类型：撰写页取左侧所选类型；材料管理页取管理页类型选择器
  const typeId =
    currentView === "materials" ? els.matUploadType.value : els.typeSelect.value;
  fd.append("type_id", typeId);
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
    } else if (ev.stage === "chat_question") {
      appendChatBubble("assistant", ev.message);
      setState(S.CLARIFYING);
    } else if (ev.stage === "chat_ready") {
      appendChatBubble("assistant", `✅ ${ev.message}。点「发送」继续补充，或点「信息够了，直接生成」。`);
      setState(S.CHOOSING);
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
  if (draft.review_summary && draft.review_summary.avg_score) {
    const avg = document.createElement("div");
    avg.className = "review-avg";
    avg.textContent = `📊 评审均分 ${draft.review_summary.avg_score}/100（${draft.review_summary.graded_sections} 章已打分）`;
    els.sectionsView.appendChild(avg);
  }
  draft.sections.forEach((sec) => {
    const card = document.createElement("div");
    card.className = "sec-card";
    card.innerHTML = `
      <div class="sec-head">
        <h3>${escapeHtml(sec.title)}${scoreBadgeHtml(sec.review)}</h3>
        <span class="word-count">${sec.word_count} 字${sec.user_revised ? " · 已修订" : ""}</span>
      </div>
      ${scoreDetailHtml(sec.review)}
      <div class="md-body">${renderMarkdown(sec.content || "")}</div>
      <details class="raw-md"><summary>查看原始 Markdown</summary><pre>${escapeHtml(sec.content || "")}</pre></details>
      <textarea class="sec-feedback" data-key="${escapeAttr(sec.key)}" rows="2"
                placeholder="对本节的修改意见（留空则本节不改）"></textarea>
    `;
    els.sectionsView.appendChild(card);
  });
  els.globalFeedback.value = "";
}

function scoreBadgeHtml(review) {
  if (!review || !review.score) return "";
  return `<span class="score-badge" data-grade="${escapeAttr(review.grade || "")}">${review.score} 分 · ${escapeHtml(review.grade || "")}</span>`;
}

function scoreDetailHtml(review) {
  if (!review || !review.score) return "";
  const dims = review.dimension_scores || {};
  const caps = { content_quality: 40, structure_logic: 30, language: 20, format_spec: 10 };
  const names = { content_quality: "内容质量", structure_logic: "结构逻辑", language: "语言表达", format_spec: "格式规范" };
  const fb = review.detailed_feedback || {};
  const lines = Object.keys(caps)
    .map((k) => {
      const v = dims[k] != null ? `${dims[k]}/${caps[k]}` : `-/${caps[k]}`;
      const comment = fb[k] ? ` — ${fb[k]}` : "";
      return `<li>${names[k]}: ${v}${escapeHtml(comment)}</li>`;
    })
    .join("");
  const notes = review.reviewer_notes ? `<p>总体评语：${escapeHtml(review.reviewer_notes)}</p>` : "";
  return `<details class="score-detail"><summary>评分明细</summary><ul>${lines}</ul>${notes}</details>`;
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

// ------------------------------------------------------------ 对话式撰写
function appendChatBubble(role, text) {
  const div = document.createElement("div");
  div.className = `chat-bubble ${role}`;
  div.textContent = text;
  els.chatMessages.appendChild(div);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
}

async function startChat() {
  resetProgress();
  els.chatMessages.innerHTML = "";
  const first = els.topicInput.value.trim();
  appendChatBubble("system", "对话式撰写：先聊聊你的需求");
  if (first) {
    appendChatBubble("user", first);
  }
  try {
    const res = await api("/chat/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type_id: els.typeSelect.value,
        message: first || "我想写一份材料",
      }),
    });
    sessionId = res.session_id;
    setState(S.CLARIFYING);
    openEvents();
  } catch (e) {
    setState(S.ERROR, e.message);
  }
}

async function sendChatMessage(force) {
  const text = els.chatInput.value.trim();
  if (!text && !force) return;
  if (text) {
    appendChatBubble("user", text);
    els.chatInput.value = "";
  }
  els.btnChatSend.disabled = true;
  els.btnChatGenerate.disabled = true;
  try {
    await api(`/chat/${sessionId}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, force: !!force }),
    });
    // 后续状态由 SSE 事件驱动（chat_question / chat_ready / start）
    if (force) setState(S.WRITING);
  } catch (e) {
    setState(S.ERROR, e.message);
  }
}

// ------------------------------------------------------------ 材料管理
function fmtSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / 1024 / 1024).toFixed(1) + " MB";
}

async function loadMaterials() {
  const tid = els.materialTypeFilter.value;
  const q = tid ? `?type_id=${encodeURIComponent(tid)}` : "";
  const res = await api(`/materials${q}`);
  renderMaterials(res.items || []);
}

function renderMaterials(items) {
  if (!items.length) {
    els.materialsList.innerHTML = '<p class="hint">暂无材料，可在上方上传。</p>';
    return;
  }
  const rows = items
    .map(
      (m) => `<tr>
        <td>${escapeHtml(m.type_name)}</td>
        <td><span class="scope-tag ${m.scope}">${m.scope === "facts" ? "事实库" : "风格库"}</span></td>
        <td>${escapeHtml(m.filename)}</td>
        <td>${fmtSize(m.size)}</td>
        <td><button class="mat-del" data-type="${escapeAttr(m.type_id)}"
            data-scope="${escapeAttr(m.scope)}" data-name="${escapeAttr(m.filename)}">删除</button></td>
      </tr>`
    )
    .join("");
  els.materialsList.innerHTML = `
    <table class="mat-table">
      <thead><tr><th>类型</th><th>库</th><th>文件</th><th>大小</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  els.materialsList.querySelectorAll(".mat-del").forEach((btn) => {
    btn.addEventListener("click", () =>
      deleteMaterial(btn.dataset.type, btn.dataset.scope, btn.dataset.name)
    );
  });
}

async function deleteMaterial(typeId, scope, filename) {
  if (!confirm(`确定删除 ${typeId}/${scope}/${filename}？`)) return;
  try {
    await api(`/materials/${encodeURIComponent(typeId)}/${encodeURIComponent(scope)}/${encodeURIComponent(filename)}`, { method: "DELETE" });
    els.materialsMsg.textContent = `已删除 ${filename}`;
    await loadMaterials();
  } catch (e) {
    alert("删除失败：" + e.message);
  }
}

async function reingestMaterials() {
  const tid = els.materialTypeFilter.value;
  const q = tid ? `?type_id=${encodeURIComponent(tid)}` : "";
  els.btnReingest.disabled = true;
  els.materialsMsg.textContent = "同步中…";
  try {
    const res = await api(`/materials/reingest${q}`, { method: "POST" });
    const parts = Object.entries(res.ingested || {})
      .map(([t, scopes]) => `${t}: facts ${scopes.facts || 0}/style ${scopes.style || 0}`)
      .join("；");
    els.materialsMsg.textContent = `已同步（${res.backend} 模式）：${parts}`;
  } catch (e) {
    els.materialsMsg.textContent = "同步失败：" + e.message;
  } finally {
    els.btnReingest.disabled = false;
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
els.btnChatStart.addEventListener("click", startChat);
els.btnChatSend.addEventListener("click", () => sendChatMessage(false));
els.btnChatGenerate.addEventListener("click", () => sendChatMessage(true));
els.btnReingest.addEventListener("click", reingestMaterials);
els.materialTypeFilter.addEventListener("change", loadMaterials);
els.navBtns.forEach((b) =>
  b.addEventListener("click", () => switchView(b.dataset.view))
);
els.chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendChatMessage(false);
  }
});

loadTypes().catch((e) => appendLog({ stage: "error", message: "类型加载失败: " + e.message }));
setState(S.IDLE);
