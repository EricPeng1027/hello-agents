/* ReportWriter Web UI 前端状态机
 *
 * 单页智能撰写：设置 → (可选)对话澄清 → 大纲确认 → 撰写/评审 → 成稿与反馈，
 * 全部在「智能撰写」视图内按状态显隐卡片推进；进度卡常驻页尾。
 *
 * 状态流转：idle → writing（阶段A：导入+规划）→ outline_review（确认大纲）
 *          → drafting（阶段B：撰写+评审）→ done ↔ revising / error
 *          对话路径：idle → clarifying → choosing → writing → …（同上）
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
let currentView = "write"; // write | materials | typeconfig

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
  cardSetup: $("card-setup"),
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
  chatTopicBanner: $("chatTopicBanner"),
  chatMessages: $("chatMessages"),
  chatInput: $("chatInput"),
  btnChatStart: $("btnChatStart"),
  btnChatSend: $("btnChatSend"),
  btnChatGenerate: $("btnChatGenerate"),
  materialTypeFilter: $("materialTypeFilter"),
  matUploadType: $("matUploadType"),
  matUploadScope: $("matUploadScope"),
  matFileInput: $("matFileInput"),
  btnMatUpload: $("btnMatUpload"),
  matUploadList: $("matUploadList"),
  materialsList: $("materialsList"),
  materialsMsg: $("materialsMsg"),
  btnReingest: $("btnReingest"),
  navBtns: document.querySelectorAll(".nav-btn"),
  viewWrite: $("view-write"),
  viewMaterials: $("view-materials"),
  viewTypeconfig: $("view-typeconfig"),
  tcTypeSelect: $("tcTypeSelect"),
  tcBadge: $("tcBadge"),
  tcOriginBadge: $("tcOriginBadge"),
  tcTypeId: $("tcTypeId"),
  tcName: $("tcName"),
  tcSystemPrompt: $("tcSystemPrompt"),
  tcRoleHint: $("tcRoleHint"),
  tcSections: $("tcSections"),
  btnTcAddSection: $("btnTcAddSection"),
  btnTcSave: $("btnTcSave"),
  btnTcReset: $("btnTcReset"),
  btnTcDelete: $("btnTcDelete"),
  btnTcNew: $("btnTcNew"),
  tcMsg: $("tcMsg"),
  tcNewModal: $("tcNewModal"),
  tcNewId: $("tcNewId"),
  tcNewName: $("tcNewName"),
  tcNewSystemPrompt: $("tcNewSystemPrompt"),
  tcNewSections: $("tcNewSections"),
  btnTcNewAddSection: $("btnTcNewAddSection"),
  btnTcNewCreate: $("btnTcNewCreate"),
  btnTcNewCancel: $("btnTcNewCancel"),
};

// ------------------------------------------------------------ 视图切换（导航）
function switchView(view) {
  currentView = view;
  els.navBtns.forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  els.viewWrite.classList.toggle("hidden", view !== "write");
  els.viewMaterials.classList.toggle("hidden", view !== "materials");
  els.viewTypeconfig.classList.toggle("hidden", view !== "typeconfig");
  if (view === "materials") loadMaterials();
  if (view === "typeconfig") loadTypeConfig();
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

  // 设置卡只在「尚未开始」时显示；流程一旦启动即收起，避免重复发起
  els.cardSetup.classList.toggle(
    "hidden",
    !(next === S.IDLE || next === S.ERROR)
  );
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
  els.tcTypeSelect.innerHTML = options;
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

// ------------------------------------------------------------ 材料上传
// 两处上传入口共用逻辑：撰写页（隐式类型=所选类型）与材料管理页（显式类型选择器）
async function uploadFilesTo({ files, scope, typeId, listEl, btn, inputEl }) {
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("scope", scope);
  fd.append("type_id", typeId);
  btn.disabled = true;
  try {
    const res = await api("/materials/upload", { method: "POST", body: fd });
    for (const name of res.saved) {
      const li = document.createElement("li");
      li.textContent = `✅ ${name}`;
      listEl.appendChild(li);
    }
    for (const r of res.rejected) {
      const li = document.createElement("li");
      li.textContent = `❌ ${r.name}：${r.reason}`;
      li.classList.add("log-error");
      listEl.appendChild(li);
    }
    inputEl.value = "";
  } catch (e) {
    alert("上传失败：" + e.message);
  } finally {
    btn.disabled = false;
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
      showChatTopic(ev.topic || "");
      setState(S.CHOOSING);
    } else if (ev.stage === "start") {
      // 对话澄清结束，进入主流水线（设置/对话卡由 setState 自动收起）
      setState(S.WRITING);
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

function showChatTopic(topic) {
  if (topic) {
    els.chatTopicBanner.textContent = `🎯 当前提炼主题：${topic}`;
    els.chatTopicBanner.classList.remove("hidden");
  } else {
    els.chatTopicBanner.classList.add("hidden");
  }
}

async function startChat() {
  const typeId = els.typeSelect.value;
  const first = els.topicInput.value.trim();
  resetProgress();
  els.chatMessages.innerHTML = "";
  showChatTopic("");
  appendChatBubble("system", "对话式撰写：先聊聊你的需求");
  if (first) {
    appendChatBubble("user", first);
  }
  try {
    const res = await api("/chat/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type_id: typeId,
        message: first || "我想写一份材料",
      }),
    });
    sessionId = res.session_id;
    setState(S.CLARIFYING);
    openEvents();
    els.cardChat.scrollIntoView({ behavior: "smooth" });
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

// ------------------------------------------------------------ 类型配置（管理器）
let tcData = null; // { effective, defaults, is_overridden, origin, ... }

function tcRowHtml(sec) {
  return `
    <td class="tc-key"><input type="text" class="tc-f-key" value="${escapeAttr(sec.key || "")}" placeholder="如 overview"></td>
    <td class="tc-title"><input type="text" class="tc-f-title" value="${escapeAttr(sec.title || "")}"></td>
    <td class="tc-words"><input type="number" class="tc-f-words" min="50" step="50" value="${sec.target_words || 400}"></td>
    <td><input type="text" class="tc-f-hints" value="${escapeAttr(sec.hints || "")}"></td>
    <td class="tc-ops">
      <button type="button" class="tc-up" title="上移">↑</button><button type="button" class="tc-down" title="下移">↓</button><button type="button" class="tc-del">删除</button>
    </td>`;
}

function tcBindRow(tr) {
  tr.querySelector(".tc-del").addEventListener("click", () => tr.remove());
  tr.querySelector(".tc-up").addEventListener("click", () => {
    const prev = tr.previousElementSibling;
    if (prev) tr.parentNode.insertBefore(tr, prev);
  });
  tr.querySelector(".tc-down").addEventListener("click", () => {
    const next = tr.nextElementSibling;
    if (next) tr.parentNode.insertBefore(next, tr);
  });
}

function tcRenderSections(tbody, sections) {
  tbody.innerHTML = "";
  sections.forEach((sec) => {
    const tr = document.createElement("tr");
    tr.innerHTML = tcRowHtml(sec);
    tcBindRow(tr);
    tbody.appendChild(tr);
  });
}

async function loadTypeConfig() {
  const tid = els.tcTypeSelect.value;
  if (!tid) return;
  els.tcMsg.textContent = "";
  try {
    tcData = await api(`/types/${encodeURIComponent(tid)}/config`);
    els.tcTypeId.value = tcData.type_id;
    els.tcName.value = tcData.effective.name || "";
    els.tcSystemPrompt.value = tcData.effective.system_prompt || "";
    els.tcRoleHint.value = tcData.effective.material_role_hint || "";
    tcRenderSections(els.tcSections, tcData.effective.sections || []);
    els.tcBadge.classList.toggle("hidden", !tcData.is_overridden);
    els.tcOriginBadge.classList.toggle("hidden", tcData.origin !== "custom");
    els.btnTcReset.disabled = !tcData.is_overridden;
  } catch (e) {
    els.tcMsg.textContent = "加载失败：" + e.message;
  }
}

function tcCollectFrom(tbody) {
  const sections = [];
  let err = null;
  tbody.querySelectorAll("tr").forEach((tr) => {
    const key = tr.querySelector(".tc-f-key").value.trim();
    const title = tr.querySelector(".tc-f-title").value.trim();
    const words = parseInt(tr.querySelector(".tc-f-words").value, 10);
    const hints = tr.querySelector(".tc-f-hints").value.trim();
    if (!key || !title) {
      err = "每章都要填「章节标识」和「标题」";
      return;
    }
    if (!Number.isInteger(words) || words <= 0) {
      err = `章节 '${key}' 的目标字数必须是正整数`;
      return;
    }
    sections.push({ key, title, target_words: words, hints });
  });
  if (err) throw new Error(err);
  if (!sections.length) throw new Error("至少保留一个章节");
  const keys = sections.map((s) => s.key);
  if (new Set(keys).size !== keys.length) throw new Error("章节标识不能重复");
  return sections;
}

async function tcSave() {
  const tid = els.tcTypeSelect.value;
  let payload;
  try {
    payload = {
      name: els.tcName.value.trim(),
      system_prompt: els.tcSystemPrompt.value,
      material_role_hint: els.tcRoleHint.value,
      sections: tcCollectFrom(els.tcSections),
    };
    if (!payload.name) throw new Error("显示名称不能为空");
  } catch (e) {
    alert(e.message);
    return;
  }
  els.btnTcSave.disabled = true;
  try {
    tcData = await api(`/types/${encodeURIComponent(tid)}/config`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    els.tcBadge.classList.toggle("hidden", !tcData.is_overridden);
    els.btnTcReset.disabled = !tcData.is_overridden;
    els.tcMsg.textContent = "✅ 已保存并生效（新撰写会话使用新结构）";
    await loadTypes(); // 各处类型下拉/章节预览同步刷新
    els.tcTypeSelect.value = tid;
  } catch (e) {
    els.tcMsg.textContent = "保存失败：" + e.message;
  } finally {
    els.btnTcSave.disabled = false;
  }
}

async function tcReset() {
  const tid = els.tcTypeSelect.value;
  if (!confirm(`确定恢复「${tid}」的默认结构？（删除覆盖配置）`)) return;
  try {
    tcData = await api(`/types/${encodeURIComponent(tid)}/config`, { method: "DELETE" });
    els.tcName.value = tcData.effective.name || "";
    els.tcSystemPrompt.value = tcData.effective.system_prompt || "";
    els.tcRoleHint.value = tcData.effective.material_role_hint || "";
    tcRenderSections(els.tcSections, tcData.effective.sections || []);
    els.tcBadge.classList.add("hidden");
    els.btnTcReset.disabled = true;
    els.tcMsg.textContent = "已恢复默认";
    await loadTypes();
    els.tcTypeSelect.value = tid;
  } catch (e) {
    els.tcMsg.textContent = "恢复失败：" + e.message;
  }
}

async function tcDelete() {
  const tid = els.tcTypeSelect.value;
  const isCustom = tcData && tcData.origin === "custom";
  const warn = isCustom
    ? `确定删除自定义类型「${tid}」？\n（定义文件删除；data/${tid}/ 下材料保留）`
    : `确定删除内置类型「${tid}」？\n（重启后保持隐藏；data/${tid}/ 下材料保留；可手工删 config/types/_deleted.yaml 恢复）`;
  if (!confirm(warn)) return;
  try {
    await api(`/types/${encodeURIComponent(tid)}`, { method: "DELETE" });
    els.tcMsg.textContent = `已删除类型 ${tid}`;
    tcData = null;
    await loadTypes();
    els.tcTypeSelect.value = els.tcTypeSelect.options[0]?.value || "";
    await loadTypeConfig();
  } catch (e) {
    els.tcMsg.textContent = "删除失败：" + e.message;
  }
}

// ------------------------------------------------------------ 新建类型弹层
function tcNewOpen() {
  els.tcNewId.value = "";
  els.tcNewName.value = "";
  els.tcNewSystemPrompt.value = "";
  tcRenderSections(els.tcNewSections, [
    { key: "overview", title: "总体概述", target_words: 400, hints: "" },
  ]);
  els.tcNewModal.classList.remove("hidden");
}

async function tcNewCreate() {
  const typeId = els.tcNewId.value.trim();
  const name = els.tcNewName.value.trim();
  if (!/^[a-z][a-z0-9_]{0,39}$/.test(typeId)) {
    alert("类型标识必须以小写字母开头，只能含小写字母/数字/下划线");
    return;
  }
  if (!name) {
    alert("显示名称不能为空");
    return;
  }
  let sections;
  try {
    sections = tcCollectFrom(els.tcNewSections);
  } catch (e) {
    alert(e.message);
    return;
  }
  els.btnTcNewCreate.disabled = true;
  try {
    await api("/types", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type_id: typeId,
        name,
        system_prompt: els.tcNewSystemPrompt.value.trim() || null,
        sections,
      }),
    });
    els.tcNewModal.classList.add("hidden");
    await loadTypes();
    els.tcTypeSelect.value = typeId;
    await loadTypeConfig();
    els.tcMsg.textContent = `✅ 已创建类型「${name}」，可继续编辑后保存`;
  } catch (e) {
    alert("创建失败：" + e.message);
  } finally {
    els.btnTcNewCreate.disabled = false;
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
els.btnUpload.addEventListener("click", () =>
  uploadFilesTo({
    files: els.fileInput.files,
    scope: els.uploadScope.value,
    typeId: els.typeSelect.value,
    listEl: els.uploadList,
    btn: els.btnUpload,
    inputEl: els.fileInput,
  })
);
els.btnMatUpload.addEventListener("click", () =>
  uploadFilesTo({
    files: els.matFileInput.files,
    scope: els.matUploadScope.value,
    typeId: els.matUploadType.value,
    listEl: els.matUploadList,
    btn: els.btnMatUpload,
    inputEl: els.matFileInput,
  })
);
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
els.tcTypeSelect.addEventListener("change", loadTypeConfig);
els.btnTcAddSection.addEventListener("click", () => {
  const tr = document.createElement("tr");
  tr.innerHTML = tcRowHtml({ key: "", title: "", target_words: 400, hints: "" });
  tcBindRow(tr);
  els.tcSections.appendChild(tr);
  tr.querySelector(".tc-f-key").focus();
});
els.btnTcSave.addEventListener("click", tcSave);
els.btnTcReset.addEventListener("click", tcReset);
els.btnTcDelete.addEventListener("click", tcDelete);
els.btnTcNew.addEventListener("click", tcNewOpen);
els.btnTcNewAddSection.addEventListener("click", () => {
  const tr = document.createElement("tr");
  tr.innerHTML = tcRowHtml({ key: "", title: "", target_words: 400, hints: "" });
  tcBindRow(tr);
  els.tcNewSections.appendChild(tr);
  tr.querySelector(".tc-f-key").focus();
});
els.btnTcNewCreate.addEventListener("click", tcNewCreate);
els.btnTcNewCancel.addEventListener("click", () =>
  els.tcNewModal.classList.add("hidden")
);
els.tcNewModal.addEventListener("click", (e) => {
  if (e.target === els.tcNewModal) els.tcNewModal.classList.add("hidden");
});

loadTypes().catch((e) => appendLog({ stage: "error", message: "类型加载失败: " + e.message }));
setState(S.IDLE);
