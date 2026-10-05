"""FastAPI entrypoint: the chat page, auth (accounts, guests, tokens) and the JSON chat API.

Run:  uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.agent.loop import AgentLoop, TurnResult
from app.auth.accounts import Account, AccountStore, AuthError
from app.auth.quota import DailyQuota
from app.auth.sessions import COOKIE_NAME, SessionSigner
from app.config import Settings, load_settings
from app.llm.router import ModelRouter
from app.tools.builtin import builtin_tools
from app.tools.registry import ToolRegistry

WEB_DIR = Path(__file__).parent / "web"
MAX_HISTORY_MESSAGES = 20  # question/answer pairs kept per user, in memory until the memory store exists
USER_SESSION_S = 14 * 86400


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    escalate: bool = False  # "think harder": use Nemotron Ultra for this turn


class TokenRequest(BaseModel):
    label: str = Field(default="", max_length=60)


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


class MeResponse(BaseModel):
    user_id: str
    kind: str
    expires_ts: float | None
    ultra_left_today: int | None  # None = no daily limit


def create_app(loop: AgentLoop | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.require_secret_key()
    if loop is None:
        loop = AgentLoop(ModelRouter(settings), ToolRegistry(settings.data_dir, builtin_tools(settings)))

    accounts = AccountStore(settings.data_dir, guest_ttl_days=settings.guest_ttl_days)
    signer = SessionSigner(settings.app_secret_key)
    quota = DailyQuota(settings.data_dir)
    accounts.cleanup_expired_guests()

    app = FastAPI(title="Personal AI")
    histories: dict[str, list[dict[str, str]]] = {}

    @app.exception_handler(AuthError)
    def auth_error(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    # ---- identity ---------------------------------------------------------------------------
    def current_account(request: Request) -> Account:
        """Bearer token (desktop companion) or session cookie (browser). 401 otherwise."""
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            account = accounts.verify_token(header[7:].strip())
        else:
            user_id = signer.verify(request.cookies.get(COOKIE_NAME))
            account = accounts.get(user_id) if user_id else None
        if account is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        return account

    def start_session(response: Response, account: Account) -> MeResponse:
        ttl = USER_SESSION_S
        if account.expires_ts is not None:
            ttl = max(0, min(ttl, account.expires_ts - accounts.clock()))
        response.set_cookie(COOKIE_NAME, signer.issue(account.user_id, ttl), max_age=int(ttl),
                            httponly=True, samesite="lax", secure=settings.cookie_secure, path="/")
        return me_for(account)

    def me_for(account: Account) -> MeResponse:
        left = None
        if account.is_guest:
            left = max(0, settings.guest_ultra_per_day - quota.used(account.user_id, "ultra"))
        return MeResponse(user_id=account.user_id, kind=account.kind,
                          expires_ts=account.expires_ts, ultra_left_today=left)

    # ---- pages ------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    # ---- auth -------------------------------------------------------------------------------
    @app.post("/api/auth/guest", response_model=MeResponse)
    def guest(response: Response) -> MeResponse:
        return start_session(response, accounts.create_guest())

    @app.post("/api/auth/register", response_model=MeResponse)
    def register(creds: Credentials, response: Response) -> MeResponse:
        return start_session(response, accounts.register(creds.username, creds.password))

    @app.post("/api/auth/login", response_model=MeResponse)
    def login(creds: Credentials, response: Response) -> MeResponse:
        return start_session(response, accounts.authenticate(creds.username, creds.password))

    @app.post("/api/auth/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"ok": True}

    @app.get("/api/me", response_model=MeResponse)
    def me(account: Account = Depends(current_account)) -> MeResponse:
        return me_for(account)

    # ---- personal access tokens (for the desktop companion) ---------------------------------
    @app.get("/api/tokens")
    def list_tokens(account: Account = Depends(current_account)) -> list[dict]:
        return accounts.list_tokens(account.user_id)

    @app.post("/api/tokens")
    def create_token(req: TokenRequest, account: Account = Depends(current_account)) -> dict[str, str]:
        token, token_id = accounts.issue_token(account.user_id, req.label)
        return {"id": token_id, "token": token, "note": "Shown once. Store it somewhere safe."}

    @app.delete("/api/tokens/{token_id}")
    def delete_token(token_id: str, account: Account = Depends(current_account)) -> dict[str, bool]:
        if not accounts.revoke_token(account.user_id, token_id):
            raise HTTPException(status_code=404, detail="No such token.")
        return {"ok": True}

    # ---- chat -------------------------------------------------------------------------------
    @app.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, account: Account = Depends(current_account)) -> ChatResponse:
        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message is empty.")
        user_id = account.user_id
        if req.escalate and account.is_guest and not quota.try_consume(
                user_id, "ultra", settings.guest_ultra_per_day):
            return ChatResponse(
                answer=(f"You've used today's {settings.guest_ultra_per_day} \"Think harder\" turns for guests. "
                        "Untick it to keep chatting with the fast model, or create an account."),
                stop_reason="quota", tool_calls=[], tokens=0, model="", tier="reasoning")

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
    def reset(account: Account = Depends(current_account)) -> dict[str, bool]:
        histories.pop(account.user_id, None)
        return {"ok": True}

    return app
