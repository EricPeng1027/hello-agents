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

from src.config import get_settings
from src.materials.loader import SUPPORTED_EXTENSIONS
from src.models import RevisionFeedback
from src.orchestrator import ReportWriterOrchestrator
from src.utils import safe_filename
from web.session import (
    HEARTBEAT_S,
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
        "sections": [
            {
                "key": s.key,
                "title": s.title,
                "content": s.content,
                "word_count": s.word_count,
                "user_revised": bool(s.metadata.get("user_revised")),
            }
            for s in draft.sections
        ],
        "export": {
            "markdown": bool(export.get("markdown")),
            "docx": bool(export.get("docx")),
        },
    }


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


@app.post("/api/materials/upload")
async def upload_materials(
    files: List[UploadFile] = File(...),
    scope: str = Form(...),
):
    """上传参考材料到 data/facts 或 data/style（撰写时被导入检索）"""
    if scope not in ("facts", "style"):
        raise HTTPException(422, "scope 必须是 facts 或 style")

    target_dir = Path(get_settings().data_dir) / scope
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
        saved.append(f"{scope}/{safe_name}")

    return {"saved": saved, "rejected": rejected}


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
