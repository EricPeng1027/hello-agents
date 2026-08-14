"""Web 会话存储：内存态会话 + SSE 事件队列

会话生命周期：writing → outline_review → drafting → done ↔ revising（/ error）。
事件同时写入 backlog（供 SSE 重连回放）与实时队列（供在线订阅）。
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# SSE 心跳间隔（秒）：防止长连接被中间层当作空闲断开
HEARTBEAT_S = 15

# 会话状态
STATUS_WRITING = "writing"          # 阶段A：导入材料 + 规划大纲
STATUS_OUTLINE = "outline_review"   # 待用户确认大纲
STATUS_DRAFTING = "drafting"        # 阶段B：逐章撰写 + 评审 + 导出
STATUS_DONE = "done"                # 成稿就绪
STATUS_REVISING = "revising"        # 按用户意见修订中
STATUS_ERROR = "error"


@dataclass
class SessionState:
    """一份在写文档的会话状态"""

    session_id: str
    type_id: str
    topic: str
    status: str = STATUS_WRITING
    outline: Optional[dict] = None
    draft: Optional[object] = None  # DocumentDraft
    error: Optional[str] = None
    events: List[dict] = field(default_factory=list)   # backlog（重连回放）
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)  # 实时订阅
    created_at: float = field(default_factory=time.time)
    _seq: int = 0


class SessionStore:
    """内存会话仓库（重启即失；导出目录才是持久产物）"""

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}
        # 正在等待大纲确认的会话：防止 A 会话 confirm 串到 B 会话的大纲
        self.pending_session_id: Optional[str] = None

    def create(self, type_id: str, topic: str) -> SessionState:
        self.gc()
        session = SessionState(
            session_id=uuid.uuid4().hex[:12], type_id=type_id, topic=topic
        )
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Optional[SessionState]:
        return self._sessions.get(session_id)

    def emit(self, session: SessionState, stage: str, message: str, **extra) -> None:
        """写入事件（backlog + 实时队列）——事件的唯一入口"""
        session._seq += 1
        event = {
            "seq": session._seq,
            "ts": round(time.time(), 1),
            "stage": stage,
            "message": message,
            **extra,
        }
        session.events.append(event)
        try:
            session.queue.put_nowait(event)
        except Exception:
            pass

    def gc(self, max_age_s: float = 4 * 3600) -> None:
        """清理过期会话（在新建会话时触发）"""
        now = time.time()
        stale = [
            sid
            for sid, s in self._sessions.items()
            if now - s.created_at > max_age_s
        ]
        for sid in stale:
            del self._sessions[sid]
