"""ReportWriter Web UI 服务

FastAPI + SSE：撰写全流程（大纲可确认/编辑）、反馈修订（全局+按章节）、
材料上传、进度推送、成稿下载。

运行：python run_web.py → http://127.0.0.1:8000

设计要点：
- 单个共享编排器 + 模块级 WRITE_LOCK：_use_split_materials/_pending/stats
  都是编排器单槽实例态，并发撰写会 race，故撰写/修订请求串行化（409 拒绝并发）
- LLM 调用是分钟级同步阻塞：流水线阶段一律 asyncio.to_thread 跑后台线程，
  进度经 orchestrator 的 progress_cb → loop.call_soon_threadsafe 推进 SSE 队列
- 会话为内存态（web/session.py），重启即失；outputs/ 导出目录是持久产物
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src import type_config
from src.config import get_settings
from src.materials.loader import SUPPORTED_EXTENSIONS
from src.models import RevisionFeedback
from src.orchestrator import ReportWriterOrchestrator
from src.prompts import CHAT_CLARIFY_PROMPT
from src.utils import safe_filename
from web.session import (
    HEARTBEAT_S,
    STATUS_CHOOSING,
    STATUS_CLARIFYING,
    STATUS_DONE,
    STATUS_DRAFTING,
    STATUS_ERROR,
    STATUS_OUTLINE,
    STATUS_REVISING,
    STATUS_WRITING,
    SessionState,
    SessionStore,
)

_BASE_DIR = Path(__file__).resolve().parent.parent

# 上传限制
MAX_FILE_BYTES = 10 * 1024 * 1024       # 单文件 10MB
MAX_TOTAL_BYTES = 50 * 1024 * 1024      # 单次请求 50MB

# 全局单例（lifespan 初始化）
orch: Optional[ReportWriterOrchestrator] = None
store = SessionStore()
# 撰写/修订串行化锁。可替换（如测试中注入可调试锁）；
# 注意：asyncio.to_thread 后台线程不能直接抛 HTTPException，
# 故锁的获取/释放在每个阶段函数内做，而不是路由里。
WRITE_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orch
    orch = ReportWriterOrchestrator(material_namespace="reportwriter_web")
    yield


app = FastAPI(title="ReportWriter Web UI", lifespan=lifespan)

# 本地开发工具：前后端同源提供，CORS 仅为灵活性保留
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ models
class WriteRequest(BaseModel):
    type_id: str
    topic: str


class OutlineSection(BaseModel):
    key: str
    title: str = ""
    summary: str = ""
    key_points: List[str] = []


class OutlineConfirm(BaseModel):
    title: str
    sections: List[OutlineSection] = []


class ReviseRequest(BaseModel):
    global_feedback: str = ""
    section_feedback: Dict[str, str] = {}


class ChatStartRequest(BaseModel):
    type_id: str
    message: str


class ChatMessageRequest(BaseModel):
    message: str
    force: bool = False  # 用户点击"直接生成"，跳过继续澄清


class TypeConfigPayload(BaseModel):
    """类型结构覆盖（保存用，全量替换该类型 YAML；None 字段不写入）"""

    name: Optional[str] = None
    system_prompt: Optional[str] = None
    material_role_hint: Optional[str] = None
    word_count_total: Optional[int] = None
    sections: Optional[List[Dict]] = None


class CustomTypePayload(BaseModel):
    """新建自定义类型（全量定义；name 与 sections 必填）"""

    type_id: str
    name: str
    system_prompt: Optional[str] = None
    material_role_hint: Optional[str] = None
    word_count_total: Optional[int] = None
    sections: List[Dict]


# ------------------------------------------------------------------ 材料管理
def _type_material_root(type_id: str) -> Path:
    """某类型的材料根目录：data/<material_base_dir>（缺省即 data/<type_id>）"""
    spec = orch.registry.get(type_id)
    if spec is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")
    base = Path(get_settings().data_dir)
    return base / (spec.material_base_dir or type_id)


def _safe_material_path(type_id: str, scope: str, filename: str) -> Path:
    """把 (类型, 角色, 文件名) 解析为服务端绝对路径（防穿越）

    文件名只允许 basename（拒绝任何目录成分），解析后必须仍落在类型目录内。
    """
    root = _type_material_root(type_id).resolve()
    if scope not in ("facts", "style"):
        raise HTTPException(422, "scope 必须是 facts 或 style")
    base = Path(filename).name
    if not base or base != filename:
        raise HTTPException(422, "非法文件名（不允许路径成分）")
    path = (root / scope / base).resolve()
    if not str(path).startswith(str(root)):
        raise HTTPException(422, "非法文件路径")
    return path


# ------------------------------------------------------------------ helpers
def _get_session(session_id: str) -> SessionState:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, f"会话不存在或已过期: {session_id}")
    return session


def _progress_cb_for(session: SessionState, loop: asyncio.AbstractEventLoop):
    """生成编排器进度回调：线程安全地推进 SSE 队列"""

    def cb(event: dict):
        stage = event.get("stage", "")
        message = event.get("message", "")
        extra = {k: v for k, v in event.items() if k not in ("stage", "message")}
        loop.call_soon_threadsafe(
            store.emit, session, stage, message, **extra
        )

    return cb


def _run_prepare_stage(session: SessionState, loop) -> None:
    """阶段A（后台线程）：导入材料 + 规划大纲（含写锁获取/释放）"""
    if not WRITE_LOCK.acquire(blocking=False):
        session.status = STATUS_ERROR
        session.error = "busy"
        store.emit(session, "error", "有撰写/修订任务进行中，请稍后再试")
        return
    try:
        outline = orch.prepare(
            session.type_id,
            session.topic,
            progress_cb=_progress_cb_for(session, loop),
        )
        session.outline = outline
        session.status = STATUS_OUTLINE
        store.pending_session_id = session.session_id
        store.emit(session, "outline_ready", "大纲已生成，等待确认")
    except Exception as e:
        session.status = STATUS_ERROR
        session.error = str(e)
        store.emit(session, "error", f"规划失败: {e}")
    finally:
        WRITE_LOCK.release()


def _run_draft_stage(session: SessionState, outline: dict, loop) -> None:
    """阶段B（后台线程）：按确认后的大纲逐章撰写 + 评审 + 导出"""
    if not WRITE_LOCK.acquire(blocking=False):
        session.status = STATUS_ERROR
        session.error = "busy"
        store.emit(session, "error", "有撰写/修订任务进行中，请稍后再试")
        store.pending_session_id = None
        return
    try:
        session.draft = orch.draft_with_outline(
            outline,
            export=True,
            progress_cb=_progress_cb_for(session, loop),
        )
        session.status = STATUS_DONE
        store.emit(session, "done", "撰写完成")
    except Exception as e:
        session.status = STATUS_ERROR
        session.error = str(e)
        store.emit(session, "error", f"撰写失败: {e}")
    finally:
        store.pending_session_id = None
        WRITE_LOCK.release()


def _run_revise_stage(session: SessionState, fb: RevisionFeedback, loop) -> None:
    """修订（后台线程）：按用户意见修订 + 重导出"""
    if not WRITE_LOCK.acquire(blocking=False):
        session.status = STATUS_ERROR
        session.error = "busy"
        store.emit(session, "error", "有撰写/修订任务进行中，请稍后再试")
        return
    try:
        orch.revise(
            session.draft,
            fb,
            reingest=False,  # 同一会话材料已在库
            export=True,
            progress_cb=_progress_cb_for(session, loop),
        )
        session.status = STATUS_DONE
        store.emit(session, "done", "修订完成")
    except Exception as e:
        session.status = STATUS_ERROR
        session.error = str(e)
        store.emit(session, "error", f"修订失败: {e}")
    finally:
        WRITE_LOCK.release()


def _validate_outline(outline: OutlineConfirm, session: SessionState) -> dict:
    """校验用户确认的大纲：章节 key 必须在 spec 骨架内，防止改名后静默丢摘要"""
    spec = orch.registry.get(session.type_id)
    valid_keys = {s.key for s in spec.sections}
    sections = []
    for sec in outline.sections:
        if sec.key not in valid_keys:
            raise HTTPException(
                422, f"未知章节 key: '{sec.key}'（可选: {sorted(valid_keys)}）"
            )
        sections.append(
            {
                "key": sec.key,
                "title": sec.title.strip(),
                "summary": sec.summary.strip(),
                "key_points": [p.strip() for p in sec.key_points if p.strip()],
            }
        )
    title = (outline.title or "").strip()
    if not title:
        raise HTTPException(422, "大纲标题不能为空")
    return {"title": title, "sections": sections}


def _serialize_draft(session: SessionState) -> dict:
    draft = session.draft
    export = (draft.meta or {}).get("export_paths") or {}
    return {
        "title": draft.title,
        "type_id": draft.type_id,
        "total_words": draft.total_words(),
        "revision_rounds": len((draft.meta or {}).get("revision_rounds", [])),
        "review_summary": (draft.meta or {}).get("review_summary"),
        "sections": [
            {
                "key": s.key,
                "title": s.title,
                "content": s.content,
                "word_count": s.word_count,
                "user_revised": bool(s.metadata.get("user_revised")),
                "review": (s.metadata or {}).get("review_result"),
            }
            for s in draft.sections
        ],
        "export": {
            "markdown": bool(export.get("markdown")),
            "docx": bool(export.get("docx")),
        },
    }


# 对话式撰写：澄清轮数软上限（超过则提示用户可直接生成）
MAX_CLARIFY_ROUNDS = 5


def _run_clarify_turn(session: SessionState) -> None:
    """对话澄清（同步，单次 LLM 调用）：提炼主题 + 生成下一追问

    结果写回 session（chat_messages / status），任何失败都兜底成 ready=True
    （避免澄清环节把用户卡死）。
    """
    spec = orch.registry.get(session.type_id)
    history_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
        for m in session.chat_messages
    )
    sections_brief = "、".join(s.title for s in spec.sections)
    prompt = CHAT_CLARIFY_PROMPT.format(
        type_name=spec.name,
        sections_brief=sections_brief,
        history=history_text,
    )
    try:
        from src.utils import JSONExtractor

        resp = orch.planner.llm.invoke(
            [
                {"role": "system", "content": "你是严谨的材料撰写需求分析助手。"},
                {"role": "user", "content": prompt},
            ]
        )
        raw = resp.content if hasattr(resp, "content") else str(resp)
        data = JSONExtractor.extract(raw, required_fields=["ready", "topic"])
        ready = bool(data.get("ready"))
        topic = str(data.get("topic") or "").strip()
        question = str(data.get("question") or "").strip()
        if topic:
            session.topic = topic
    except Exception as e:
        print(f"▸️  澄清解析失败，直接生成: {e}")
        ready, question = True, ""

    if ready:
        session.status = STATUS_CHOOSING
        store.emit(session, "chat_ready", f"需求已明确：{session.topic}", topic=session.topic)
    else:
        session.status = STATUS_CLARIFYING
        if not question:
            question = "请再补充一些关键信息（时间范围/部门/重点成果）？"
        session.chat_messages.append({"role": "assistant", "content": question})
        store.emit(session, "chat_question", question)


def _run_clarify_stage(session: SessionState, user_message: str, loop) -> None:
    """一轮澄清（后台线程）：带会话状态锁"""
    if not WRITE_LOCK.acquire(blocking=False):
        store.emit(session, "error", "有撰写/修订任务进行中，请稍后再试")
        return
    try:
        session.chat_messages.append({"role": "user", "content": user_message})
        _run_clarify_turn(session)
    finally:
        WRITE_LOCK.release()


def _kickoff_prepare(session: SessionState, loop) -> None:
    """从对话进入撰写：确认主题 → 阶段A（导入+规划）"""
    session.status = STATUS_WRITING
    store.emit(session, "start", f"开始撰写：{session.topic}")
    asyncio.create_task(asyncio.to_thread(_run_prepare_stage, session, loop))


# ------------------------------------------------------------------ routes
@app.get("/api/types")
def list_types():
    """材料类型清单（含章节骨架预览）"""
    result = []
    for type_id in orch.registry.list_types():
        spec = orch.registry.get(type_id)
        result.append(
            {
                "type_id": spec.type_id,
                "name": spec.name,
                "paradigm": spec.paradigm,
                "origin": "custom" if orch.registry.is_custom(type_id) else "builtin",
                "is_overridden": type_config.has_override(type_id),
                "sections": [
                    {
                        "key": s.key,
                        "title": s.title,
                        "target_words": s.target_words,
                        "hints": s.hints,
                    }
                    for s in spec.sections
                ],
            }
        )
    return result


# ------------------------------------------------------------------ 类型配置
def _serialize_type_config(type_id: str) -> dict:
    """生效配置（默认+覆盖合并结果）+ 覆盖状态 + 内置默认（供编辑器对照）"""
    spec = orch.registry.get(type_id)
    default = orch.registry.get_default(type_id)
    if spec is None or default is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")

    def secs(s):
        return [
            {
                "key": x.key,
                "title": x.title,
                "required": x.required,
                "target_words": x.target_words,
                "hints": x.hints,
            }
            for x in s.sections
        ]

    return {
        "type_id": type_id,
        "name": spec.name,
        "origin": "custom" if orch.registry.is_custom(type_id) else "builtin",
        "is_overridden": type_config.has_override(type_id),
        "effective": {
            "name": spec.name,
            "system_prompt": spec.system_prompt,
            "material_role_hint": spec.material_role_hint,
            "word_count_total": spec.word_count_total,
            "sections": secs(spec),
        },
        "defaults": {
            "name": default.name,
            "system_prompt": default.system_prompt,
            "material_role_hint": default.material_role_hint,
            "word_count_total": default.word_count_total,
            "sections": secs(default),
        },
    }


@app.get("/api/types/{type_id}/config")
def get_type_config(type_id: str):
    """类型结构配置：生效值 + 是否覆盖 + 内置默认"""
    return _serialize_type_config(type_id)


@app.put("/api/types/{type_id}/config")
def put_type_config(type_id: str, payload: TypeConfigPayload):
    """保存类型结构覆盖：写 YAML + 注册表热更新（与撰写互斥）"""
    if orch.registry.get(type_id) is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")
    if not WRITE_LOCK.acquire(blocking=False):
        raise HTTPException(409, "有撰写/修订任务进行中，请稍后再试")
    try:
        try:
            type_config.save_type_override(
                type_id, payload.model_dump(exclude_unset=True)
            )
        except type_config.TypeConfigError as e:
            raise HTTPException(422, str(e))
        orch.registry.refresh_type(type_id)
    finally:
        WRITE_LOCK.release()
    return _serialize_type_config(type_id)


@app.delete("/api/types/{type_id}/config")
def delete_type_config(type_id: str):
    """恢复默认：删除覆盖 YAML + 注册表热更新"""
    if orch.registry.get(type_id) is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")
    if not WRITE_LOCK.acquire(blocking=False):
        raise HTTPException(409, "有撰写/修订任务进行中，请稍后再试")
    try:
        type_config.delete_type_override(type_id)
        orch.registry.refresh_type(type_id)
    finally:
        WRITE_LOCK.release()
    return _serialize_type_config(type_id)


# ------------------------------------------------------------------ 类型的增删
@app.post("/api/types", status_code=201)
def create_type(payload: CustomTypePayload):
    """新建自定义类型：全量 YAML 定义 + 注册表热更新（与撰写互斥）"""
    try:
        type_config.validate_type_id(payload.type_id)
    except type_config.TypeConfigError as e:
        raise HTTPException(422, str(e))
    if orch.registry.get(payload.type_id) is not None:
        raise HTTPException(409, f"类型已存在: {payload.type_id}")
    if payload.type_id in orch.registry.list_deleted_builtin():
        raise HTTPException(409, f"'{payload.type_id}' 是已删除的内置类型，请先在材料管理中恢复或换用其他标识")
    if not WRITE_LOCK.acquire(blocking=False):
        raise HTTPException(409, "有撰写/修订任务进行中，请稍后再试")
    try:
        data = payload.model_dump(exclude={"type_id"})
        try:
            type_config.save_custom_type(payload.type_id, data)
        except type_config.TypeConfigError as e:
            raise HTTPException(422, str(e))
        orch.registry.register_custom(
            payload.type_id, type_config.load_custom_type(payload.type_id)
        )
    finally:
        WRITE_LOCK.release()
    return _serialize_type_config(payload.type_id)


@app.delete("/api/types/{type_id}")
def delete_type(type_id: str):
    """删除类型：自定义删定义；内置加隐藏标记（磁盘材料目录保留）"""
    if orch.registry.get(type_id) is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")
    if not WRITE_LOCK.acquire(blocking=False):
        raise HTTPException(409, "有撰写/修订任务进行中，请稍后再试")
    try:
        kind = orch.registry.delete_type(type_id)
    finally:
        WRITE_LOCK.release()
    return {"ok": True, "deleted": type_id, "kind": kind,
            "note": "磁盘材料目录保留" if kind == "builtin" else "定义已删除，材料目录保留"}


@app.post("/api/materials/upload")
async def upload_materials(
    files: List[UploadFile] = File(...),
    scope: str = Form(...),
    type_id: str = Form(...),
):
    """上传参考材料到指定类型的 facts/style 库（撰写时按类型导入检索）"""
    if scope not in ("facts", "style"):
        raise HTTPException(422, "scope 必须是 facts 或 style")

    target_dir = _type_material_root(type_id) / scope
    target_dir.mkdir(parents=True, exist_ok=True)

    saved, rejected = [], []
    total_bytes = 0
    for f in files:
        name = Path(f.filename or "").name
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            rejected.append(
                {"name": name, "reason": f"不支持的格式 {ext}（支持 {sorted(SUPPORTED_EXTENSIONS)}）"}
            )
            continue
        content = await f.read()
        total_bytes += len(content)
        if len(content) > MAX_FILE_BYTES:
            rejected.append({"name": name, "reason": "单文件超过 10MB"})
            continue
        if total_bytes > MAX_TOTAL_BYTES:
            rejected.append({"name": name, "reason": "本次上传总量超过 50MB"})
            continue
        safe_name = safe_filename(Path(name).stem) + ext
        (target_dir / safe_name).write_bytes(content)
        saved.append(f"{type_id}/{scope}/{safe_name}")

    return {"saved": saved, "rejected": rejected}


@app.get("/api/materials")
def list_materials(type_id: Optional[str] = None):
    """材料清单（管理页数据源）

    不传 type_id 时返回所有类型；返回每项材料的类型/角色/大小/修改时间。
    """
    type_ids = [type_id] if type_id else orch.registry.list_types()
    if type_id and orch.registry.get(type_id) is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")
    items = []
    for tid in type_ids:
        spec = orch.registry.get(tid)
        root = _type_material_root(tid)
        for scope in ("facts", "style"):
            d = root / scope
            if not d.is_dir():
                continue
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
                    items.append(
                        {
                            "type_id": tid,
                            "type_name": spec.name,
                            "scope": scope,
                            "filename": f.name,
                            "size": f.stat().st_size,
                            "modified": round(f.stat().st_mtime, 1),
                        }
                    )
    return {"items": items, "backend": orch.material.effective_mode}


@app.delete("/api/materials/{type_id}/{scope}/{filename}")
def delete_material(type_id: str, scope: str, filename: str):
    """删除指定材料文件（RAG 库中对应向量建议通过 reingest 清理）"""
    path = _safe_material_path(type_id, scope, filename)
    if not path.is_file():
        raise HTTPException(404, f"材料不存在: {filename}")
    path.unlink()
    return {"ok": True, "deleted": f"{type_id}/{scope}/{filename}"}


@app.post("/api/materials/reingest")
def reingest_materials(type_id: Optional[str] = None):
    """按类型把磁盘材料导入检索库（管理页"同步到检索库"按钮）

    RAG 模式 point ID 按 (namespace,文件名,块号) uuid5 幂等；
    本地后端按路径幂等，故重复调用不会重复导入。
    """
    type_ids = [type_id] if type_id else orch.registry.list_types()
    if type_id and orch.registry.get(type_id) is None:
        raise HTTPException(422, f"未知材料类型: {type_id}")
    results = {}
    for tid in type_ids:
        spec = orch.registry.get(tid)
        root = _type_material_root(tid)
        ns = spec.material_base_dir or ""
        per_type = {}
        for scope in ("facts", "style"):
            d = root / scope
            if d.is_dir():
                r = orch.material.ingest(str(d), scope=orch._scope(ns, scope))
                per_type[scope] = r.get("success", 0)
        results[tid] = per_type
    return {"ok": True, "ingested": results, "backend": orch.material.effective_mode}


@app.post("/api/write")
async def start_write(req: WriteRequest):
    """开始撰写：建会话 → 后台跑阶段A（导入+规划），完成后待确认大纲"""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(422, "主题不能为空")
    if orch.registry.get(req.type_id) is None:
        raise HTTPException(422, f"未知材料类型: {req.type_id}")

    session = store.create(req.type_id, topic)
    session.status = STATUS_WRITING
    loop = asyncio.get_running_loop()
    store.emit(session, "start", f"开始撰写：{topic}")
    asyncio.create_task(asyncio.to_thread(_run_prepare_stage, session, loop))
    return {"session_id": session.session_id}


# ------------------------------------------------------------------ 对话式撰写
@app.post("/api/chat/start")
async def chat_start(req: ChatStartRequest):
    """对话式撰写入口：建会话 → 后台跑第一轮澄清"""
    message = req.message.strip()
    if not message:
        raise HTTPException(422, "请先描述你的撰写需求")
    if orch.registry.get(req.type_id) is None:
        raise HTTPException(422, f"未知材料类型: {req.type_id}")

    session = store.create(req.type_id, topic=message[:80])
    session.status = STATUS_CLARIFYING
    loop = asyncio.get_running_loop()
    store.emit(session, "chat_start", "已收到需求，正在理解…")
    asyncio.create_task(asyncio.to_thread(_run_clarify_stage, session, message, loop))
    return {"session_id": session.session_id}


@app.post("/api/chat/{session_id}/message")
async def chat_message(session_id: str, req: ChatMessageRequest):
    """对话消息：继续澄清；force=true 或超轮次时直接进入撰写"""
    session = _get_session(session_id)
    if session.status not in (STATUS_CLARIFYING, STATUS_CHOOSING):
        raise HTTPException(409, "当前状态不可继续对话: " + session.status)

    message = req.message.strip()
    if not message and not req.force:
        raise HTTPException(422, "消息不能为空")

    loop = asyncio.get_running_loop()
    user_turns = sum(1 for m in session.chat_messages if m["role"] == "user")

    if req.force or session.status == STATUS_CHOOSING:
        if message and session.status != STATUS_CHOOSING:
            session.chat_messages.append({"role": "user", "content": message})
        _kickoff_prepare(session, loop)
        return {"ok": True, "decision": "generate"}

    if user_turns >= MAX_CLARIFY_ROUNDS:
        session.chat_messages.append({"role": "user", "content": message})
        session.status = STATUS_CHOOSING
        store.emit(session, "chat_ready", f"信息已足够：{session.topic}", topic=session.topic)
        return {"ok": True, "decision": "ready"}

    asyncio.create_task(asyncio.to_thread(_run_clarify_stage, session, message, loop))
    return {"ok": True, "decision": "clarify"}


@app.get("/api/chat/{session_id}")
def chat_state(session_id: str):
    """对话状态（消息历史 + 当前提炼主题）"""
    session = _get_session(session_id)
    return {
        "status": session.status,
        "topic": session.topic,
        "messages": session.chat_messages,
    }


@app.get("/api/outline/{session_id}")
def get_outline(session_id: str):
    """取大纲（供用户确认/编辑）"""
    session = _get_session(session_id)
    if session.status != STATUS_OUTLINE or not session.outline:
        raise HTTPException(409, "大纲尚未生成（当前状态: " + session.status + "）")
    return session.outline


@app.post("/api/outline/{session_id}/confirm")
async def confirm_outline(session_id: str, outline: OutlineConfirm):
    """确认大纲 → 后台跑阶段B（撰写+评审+导出）"""
    session = _get_session(session_id)
    if session.status != STATUS_OUTLINE:
        raise HTTPException(409, "当前状态不可确认大纲: " + session.status)
    if store.pending_session_id != session.session_id:
        raise HTTPException(409, "该会话的大纲已被其他任务占用，请重新开始")

    validated = _validate_outline(outline, session)
    session.status = STATUS_DRAFTING
    loop = asyncio.get_running_loop()
    store.emit(session, "draft", "大纲已确认，开始逐章撰写")
    asyncio.create_task(
        asyncio.to_thread(_run_draft_stage, session, validated, loop)
    )
    return {"ok": True}


@app.get("/api/draft/{session_id}")
def get_draft(session_id: str):
    """取成稿（章节内容 + 字数 + 修订轮次）"""
    session = _get_session(session_id)
    if session.draft is None or session.status not in (STATUS_DONE, STATUS_REVISING):
        raise HTTPException(409, "成稿尚未生成（当前状态: " + session.status + "）")
    return _serialize_draft(session)


@app.post("/api/revise/{session_id}")
async def revise_draft(session_id: str, req: ReviseRequest):
    """提交修改意见 → 后台修订（全局 + 按章节细粒度）"""
    session = _get_session(session_id)
    if session.draft is None or session.status != STATUS_DONE:
        raise HTTPException(409, "当前状态不可修订: " + session.status)

    fb = RevisionFeedback(
        global_feedback=req.global_feedback,
        section_feedback=req.section_feedback or {},
    )
    if fb.is_empty():
        raise HTTPException(422, "修改意见为空，请填写全局意见或至少一节的意见")

    session.status = STATUS_REVISING
    loop = asyncio.get_running_loop()
    store.emit(session, "revise", "收到修改意见，开始修订")
    asyncio.create_task(asyncio.to_thread(_run_revise_stage, session, fb, loop))
    return {"ok": True}


@app.get("/api/events/{session_id}")
async def events(session_id: str):
    """SSE 进度流：先回放 backlog，再实时推送；15s 心跳保活"""
    session = _get_session(session_id)

    async def event_generator():
        # 回放 backlog（支持重连后补齐）
        for ev in session.events:
            yield {"data": _to_json(ev)}
        # 实时推送
        while True:
            try:
                ev = await asyncio.wait_for(
                    session.queue.get(), timeout=HEARTBEAT_S
                )
                yield {"data": _to_json(ev)}
            except asyncio.TimeoutError:
                yield {"comment": "ping"}

    return EventSourceResponse(event_generator())


def _to_json(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


@app.get("/api/download/{session_id}/{kind}")
def download(session_id: str, kind: str):
    """下载成稿（markdown/docx）；路径由服务端从会话导出记录解析，防穿越"""
    session = _get_session(session_id)
    if session.draft is None:
        raise HTTPException(409, "成稿尚未生成")
    if kind not in ("markdown", "docx"):
        raise HTTPException(422, "kind 必须是 markdown 或 docx")
    export = (session.draft.meta or {}).get("export_paths") or {}
    path = export.get(kind)
    if not path or not Path(path).is_file():
        raise HTTPException(404, f"{kind} 文件不存在（可能导出失败）")
    return FileResponse(path, filename=Path(path).name)


@app.get("/api/status/{session_id}")
def status(session_id: str):
    session = _get_session(session_id)
    return {
        "status": session.status,
        "error": session.error,
        "has_draft": session.draft is not None,
        "has_outline": session.outline is not None,
    }


# ------------------------------------------------------------------ static
_outputs_dir = Path(get_settings().output_dir)
_outputs_dir.mkdir(parents=True, exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(_outputs_dir)), name="outputs")
# 静态前端最后挂载（html=True → / 直接返回 index.html）
app.mount(
    "/",
    StaticFiles(directory=str(Path(__file__).parent / "static"), html=True),
    name="static",
)
