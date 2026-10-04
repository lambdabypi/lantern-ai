"""FastAPI entrypoint: a single chat page plus a JSON chat API.

Run:  uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent.loop import AgentLoop, TurnResult
from app.auth.users import DEMO_USER_ID
from app.config import load_settings
from app.llm.router import ModelRouter
from app.tools.builtin import builtin_tools
from app.tools.registry import ToolRegistry

WEB_DIR = Path(__file__).parent / "web"
MAX_HISTORY_MESSAGES = 20  # question/answer pairs kept per user, in memory until the memory store exists


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    escalate: bool = False  # "think harder": use Nemotron Ultra for this turn


class ToolCallOut(BaseModel):
    name: str
    status: str
    duration_ms: int


class ChatResponse(BaseModel):
    answer: str
    stop_reason: str
    tool_calls: list[ToolCallOut]
    tokens: int
    model: str
    tier: str


def current_user() -> str:
    """Until auth exists (build step 5), everyone is the demo user."""
    return DEMO_USER_ID


def create_app(loop: AgentLoop | None = None) -> FastAPI:
    if loop is None:
        settings = load_settings()
        loop = AgentLoop(ModelRouter(settings), ToolRegistry(settings.data_dir, builtin_tools(settings)))

    app = FastAPI(title="Personal AI")
    histories: dict[str, list[dict[str, str]]] = {}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, user_id: str = Depends(current_user)) -> ChatResponse:
        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message is empty.")
        history = histories.setdefault(user_id, [])
        turn: TurnResult = loop.run_turn(user_id, message, history, escalate=req.escalate)
        if turn.stop_reason == "done":
            history.extend(turn.history_messages(message))
            del history[:-MAX_HISTORY_MESSAGES]
        return ChatResponse(
            answer=turn.answer,
            stop_reason=turn.stop_reason,
            tool_calls=[ToolCallOut(**vars(c)) for c in turn.tool_calls],
            tokens=turn.total_tokens,
            model=turn.model,
            tier=turn.tier,
        )

    @app.post("/api/reset")
    def reset(user_id: str = Depends(current_user)) -> dict[str, bool]:
        histories.pop(user_id, None)
        return {"ok": True}

    return app
