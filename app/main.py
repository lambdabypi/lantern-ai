"""FastAPI entrypoint: the chat page, auth (accounts, guests, tokens) and the JSON chat API.

Run:  uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from dataclasses import asdict
from pathlib import Path
from queue import Queue
from urllib.parse import quote

import httpx

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response as RawResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.lookup import LookupPlanner
from app.agent.loop import AgentLoop, TurnResult
from app.auth.accounts import Account, AccountStore, AuthError
from app.auth.quota import DailyQuota
from app.auth import steam_openid
from app.auth.sessions import COOKIE_NAME, SessionSigner
from app.config import Settings, load_settings
from app.llm.router import ModelRouter, Tier
from app.memory.extractor import MemoryExtractor
from app.memory.history import HistoryStore
from app.memory.store import KINDS, MemoryStore, MemoryStoreError
from app.steam_import import import_steam_library
from app.tools.builtin import builtin_tools
from app.tools.game_knowledge import GameKnowledge, make_game_tools
from app.tools.profile_tools import make_profile_tools
from app.tools.steam import SteamClient, SteamError
from app.tools.web_search import WebSearch
from app.tools.registry import ToolRegistry

log = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
MAX_HISTORY_MESSAGES = 20  # recent messages sent to the model each turn
USER_SESSION_S = 14 * 86400
STEAM_STATE_COOKIE = "pai_steam_state"


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


class MemoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    fields: dict = Field(default_factory=dict)
    note: str = Field(default="", max_length=2000)


class MemoryText(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class SteamImportRequest(BaseModel):
    profile: str = Field(min_length=2, max_length=200)


class MeResponse(BaseModel):
    user_id: str
    kind: str
    expires_ts: float | None
    ultra_left_today: int | None  # None = no daily limit
    display_name: str | None = None
    steam_enabled: bool = False


def create_app(loop: AgentLoop | None = None, settings: Settings | None = None,
               steam: SteamClient | None = None, openid_http: httpx.Client | None = None,
               extractor: MemoryExtractor | None = None, knowledge: GameKnowledge | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.require_secret_key()
    memory = MemoryStore(settings.data_dir)
    history_store = HistoryStore(settings.data_dir)
    if loop is None:
        router = ModelRouter(settings)
        search = WebSearch(settings)
        knowledge = knowledge or GameKnowledge(memory, search, settings.data_dir)
        tools = (make_game_tools(knowledge) + builtin_tools(settings, search)
                 + make_profile_tools(memory, knowledge.cached_sources))
        # Thinking off for the chat model: 2-10x faster in a live comparison and still used the calculator
        # for arithmetic. Saving memories is the extractor's job, so it no longer depends on this model.
        # The planner decides game lookups up front: with thinking off the chat model often skipped
        # ask_game and invented facts, so that decision is no longer left to it.
        loop = AgentLoop(router, ToolRegistry(settings.data_dir, tools),
                         profile_context=memory.context_for_prompt, plan_thinking=False,
                         planner=LookupPlanner(router, memory.context_for_prompt), answer_tier=Tier.BALANCED)
        extractor = extractor or MemoryExtractor(router, memory, knowledge.cached_sources,
                                                 on_games_saved=knowledge.prewarm)

    steam_client = steam or (SteamClient(settings.steam_api_key) if settings.steam_api_key else None)
    accounts = AccountStore(settings.data_dir, guest_ttl_days=settings.guest_ttl_days)
    openid_clients: list[httpx.Client] = [openid_http] if openid_http else []  # created on first Steam sign-in
    signer = SessionSigner(settings.app_secret_key)
    quota = DailyQuota(settings.data_dir)
    accounts.cleanup_expired_guests()

    app = FastAPI(title="Lantern")

    @app.exception_handler(AuthError)
    def auth_error(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(MemoryStoreError)
    def memory_error(_: Request, e: MemoryStoreError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

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
        return MeResponse(user_id=account.user_id, kind=account.kind, expires_ts=account.expires_ts,
                          ultra_left_today=left, steam_enabled=steam_client is not None,
                          display_name=account.display_name)

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

    # ---- Sign in through Steam (OpenID 2.0) --------------------------------------------------
    def base_url(request: Request) -> str:
        return settings.public_base_url or str(request.base_url).rstrip("/")

    @app.get("/auth/steam/start", include_in_schema=False)
    def steam_start(request: Request) -> RedirectResponse:
        state = secrets.token_urlsafe(16)  # ties Steam's answer to this browser (stops login CSRF)
        base = base_url(request)
        response = RedirectResponse(
            steam_openid.login_url(f"{base}/auth/steam/callback?state={state}", base), status_code=303)
        response.set_cookie(STEAM_STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax",
                            secure=settings.cookie_secure, path="/auth/steam")
        return response

    @app.get("/auth/steam/callback", include_in_schema=False)
    def steam_callback(request: Request, background: BackgroundTasks) -> RedirectResponse:
        def fail(message: str) -> RedirectResponse:
            r = RedirectResponse("/?auth_error=" + quote(message), status_code=303)
            r.delete_cookie(STEAM_STATE_COOKIE, path="/auth/steam")
            return r

        params = dict(request.query_params)
        state = params.get("state", "")
        if not state or not hmac.compare_digest(state, request.cookies.get(STEAM_STATE_COOKIE, "")):
            return fail("That Steam sign-in expired or was started somewhere else. Please try again.")
        try:
            if not openid_clients:
                openid_clients.append(httpx.Client(timeout=steam_openid.TIMEOUT_S))
            steam_id = steam_openid.verify(params, f"{base_url(request)}/auth/steam/callback?state={state}",
                                           openid_clients[0])
        except AuthError as e:
            return fail(str(e))
        account, created = accounts.get_or_create_steam(
            steam_id, steam_client.player_name(steam_id) if steam_client else None)
        if created and steam_client:
            try:  # first sign-in: bring their library in straight away
                import_steam_library(memory, steam_client, account.user_id, steam_id)
                prewarm_later(background, account.user_id, unsourced_games(account.user_id))
            except SteamError:
                pass  # e.g. private game details; they can import later from My games
        response = RedirectResponse("/", status_code=303)
        start_session(response, account)
        response.delete_cookie(STEAM_STATE_COOKIE, path="/auth/steam")
        return response

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
    def check_message(req: ChatRequest) -> str:
        message = req.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message is empty.")
        return message

    def quota_block(req: ChatRequest, account: Account) -> ChatResponse | None:
        if req.escalate and account.is_guest and not quota.try_consume(
                account.user_id, "ultra", settings.guest_ultra_per_day):
            return ChatResponse(
                answer=(f"You've used today's {settings.guest_ultra_per_day} \"Think harder\" turns for guests. "
                        "Untick it to keep chatting with the fast model, or create an account."),
                stop_reason="quota", tool_calls=[], tokens=0, model="", tier="reasoning")
        return None

    def run_chat_turn(req: ChatRequest, account: Account, message: str, on_event=None) -> ChatResponse:
        user_id = account.user_id
        history = history_store.recent(user_id, MAX_HISTORY_MESSAGES)
        turn: TurnResult = loop.run_turn(user_id, message, history, escalate=req.escalate, on_event=on_event)
        if turn.stop_reason == "done":
            history_store.append(user_id, turn.history_messages(message))
        return ChatResponse(
            answer=turn.answer,
            stop_reason=turn.stop_reason,
            tool_calls=[ToolCallOut(**vars(c)) for c in turn.tool_calls],
            tokens=turn.total_tokens,
            model=turn.model,
            tier=turn.tier,
        )

    @app.post("/api/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, background: BackgroundTasks,
             account: Account = Depends(current_account)) -> ChatResponse:
        message = check_message(req)
        if (blocked := quota_block(req, account)) is not None:
            return blocked
        response = run_chat_turn(req, account, message)
        if extractor is not None:  # saves games/progress/creators after the reply is sent: no extra wait
            background.add_task(extractor.run, account.user_id, message)
        return response

    @app.post("/api/chat/stream")
    def chat_stream(req: ChatRequest, background: BackgroundTasks,
                    account: Account = Depends(current_account)) -> StreamingResponse:
        """Server-sent events: {"type": "status"|"delta"|"done"|"error", ...}. The turn runs in a worker
        thread so status lines and answer text reach the browser the moment they exist."""
        message = check_message(req)
        blocked = quota_block(req, account)

        def events():
            if blocked is not None:
                yield _sse({"type": "done", **blocked.model_dump()})
                return
            queue: Queue = Queue()

            def work() -> None:
                try:
                    done = run_chat_turn(req, account, message, on_event=queue.put)
                    queue.put({"type": "done", **done.model_dump()})
                except Exception:  # noqa: BLE001 - report it to the browser instead of hanging the stream
                    log.exception("streamed chat turn failed")
                    queue.put({"type": "error", "text": "Something went wrong on our side. Please try again."})
                finally:
                    queue.put(None)

            threading.Thread(target=work, daemon=True).start()
            while (event := queue.get()) is not None:
                yield _sse(event)

        if extractor is not None and blocked is None:
            background.add_task(extractor.run, account.user_id, message)
        return StreamingResponse(events(), media_type="text/event-stream", background=background,
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/reset")
    def reset(account: Account = Depends(current_account)) -> dict[str, bool]:
        history_store.clear(account.user_id)
        return {"ok": True}

    @app.get("/api/history")
    def history(account: Account = Depends(current_account)) -> list[dict[str, str]]:
        return history_store.recent(account.user_id, MAX_HISTORY_MESSAGES)

    # ---- visible memory ("Your gaming profile") ---------------------------------------------
    def check_kind(kind: str) -> str:
        if kind not in (*KINDS, "profile"):
            raise HTTPException(status_code=404, detail="Unknown memory type.")
        return kind

    @app.get("/api/profile")
    def profile(account: Account = Depends(current_account)) -> dict:
        uid = account.user_id
        prof = memory.get(uid, "profile")
        return {"profile": prof.summary() if prof else None,
                "games": [g.summary() for g in memory.list(uid, "game")],
                "creators": [c.summary() for c in memory.list(uid, "creator")]}

    @app.post("/api/memory/{kind}")
    def create_memory(kind: str, req: MemoryCreate, background: BackgroundTasks,
                      account: Account = Depends(current_account)) -> dict:
        item = memory.upsert(account.user_id, check_kind(kind), req.name, req.fields, note=req.note, source="user")
        if kind == "game":
            prewarm_later(background, account.user_id, [item.meta.get("title", req.name)])
        return item.summary()

    @app.get("/api/memory/{kind}/{slug}")
    def read_memory(kind: str, slug: str, account: Account = Depends(current_account)) -> dict[str, str]:
        text = memory.raw(account.user_id, check_kind(kind), None if kind == "profile" else slug)
        if text is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return {"text": text}

    @app.put("/api/memory/{kind}/{slug}")
    def write_memory(kind: str, slug: str, req: MemoryText, account: Account = Depends(current_account)) -> dict:
        item = memory.save_raw(account.user_id, check_kind(kind), None if kind == "profile" else slug, req.text)
        return item.summary()

    @app.delete("/api/memory/{kind}/{slug}")
    def delete_memory(kind: str, slug: str, account: Account = Depends(current_account)) -> dict[str, bool]:
        if not memory.delete(account.user_id, check_kind(kind), None if kind == "profile" else slug):
            raise HTTPException(status_code=404, detail="Not found.")
        return {"ok": True}

    def unsourced_games(user_id: str) -> list[str]:
        return [g.meta.get("title", g.slug) for g in memory.list(user_id, "game") if not g.meta.get("sources")]

    def prewarm_later(background: BackgroundTasks, user_id: str, titles: list[str]) -> None:
        """Find wiki sources for these games after the response is sent (first questions get faster)."""
        if knowledge is not None and titles:
            background.add_task(knowledge.prewarm, user_id, titles)

    @app.post("/api/steam/import")
    def steam_import(req: SteamImportRequest, background: BackgroundTasks,
                     account: Account = Depends(current_account)) -> dict:
        if steam_client is None:
            raise HTTPException(status_code=503, detail="Steam import isn't set up on this server (no STEAM_API_KEY).")
        try:
            result = import_steam_library(memory, steam_client, account.user_id, req.profile)
        except SteamError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        prewarm_later(background, account.user_id, unsourced_games(account.user_id))
        return asdict(result)

    @app.get("/api/export")
    def export(account: Account = Depends(current_account)) -> RawResponse:
        return RawResponse(memory.export_zip(account.user_id), media_type="application/zip",
                           headers={"Content-Disposition": 'attachment; filename="my-gaming-memory.zip"'})

    return app


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
