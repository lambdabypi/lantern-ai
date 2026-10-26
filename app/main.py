"""FastAPI entrypoint: the app page, auth (accounts, guests, tokens), chat, the recall watch and push.

Run:  uvicorn app.main:create_app --factory --reload
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from pathlib import Path
from queue import Queue

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response as RawResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.agent.recall_planner import RecallPlanner
from app.agent.loop import AgentLoop, TurnResult
from app.auth.accounts import Account, AccountStore, AuthError
from app.auth.quota import DailyQuota, SiteQuota
from app.auth.sessions import COOKIE_NAME, SessionSigner
from app.config import Settings, load_settings
from app.llm.router import ModelRouter, Tier
from app.memory.history import HistoryStore
from app.memory.store import KINDS, RECALL_STATUSES, UNIT_CHECKS, MemoryStore, MemoryStoreError
from app.demo import load_demo_household
from app.fix_helper import draft_message, find_dealers
from app.followups import FollowUps, daily_alerts
from app.owner_reports import OwnerReports
from app.photos import PhotoError
from app.precheck import PreCheck, PreCheckError, make_precheck_tool
from app.receipts import MAX_THINGS as MAX_RECEIPT_THINGS, ReceiptError, ReceiptReader
from app.push import Push, PushError
from app.skill_files import SkillOff, Skills
from app.recall_watch import RecallWatch
from app.tools.builtin import builtin_tools
from app.maker_pages import MakerError, MakerPages, make_ask_maker_tool
from app.tools.recall_tools import make_recall_tools, save_things
from app.tools.recalls import RecallClient, RecallError
from app.tools.web_search import WebSearch
from app.tools.registry import ToolRegistry
from app.unit_check import UnitCheck, UnitCheckError

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    """Send the app's own INFO logs to stderr (Cloud Run collects them); uvicorn configures only its loggers."""
    app_log = logging.getLogger("app")
    if not app_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        app_log.addHandler(handler)
        app_log.setLevel(logging.INFO)
        app_log.propagate = False


WEB_DIR = Path(__file__).parent / "web"
ICONS = {"icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png"}
FONTS = {"geist-latin-wght-normal.woff2", "instrument-serif-latin-400-normal.woff2",
         "instrument-serif-latin-400-italic.woff2", "OFL-geist.txt", "OFL-instrument-serif.txt"}  # all SIL OFL 1.1
MAX_HISTORY_MESSAGES = 20  # recent messages sent to the model each turn
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


class MemoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    fields: dict = Field(default_factory=dict)
    note: str = Field(default="", max_length=2000)


class VehicleCreate(BaseModel):
    owner: str = Field(default="", max_length=40)   # someone the user looks after ("Mom"); empty = theirs
    vin: str = Field(default="", max_length=30)
    make: str = Field(default="", max_length=40)
    model: str = Field(default="", max_length=40)
    year: int | None = Field(default=None, ge=1950, le=2100)


class RemedyRequest(BaseModel):
    kind: str = Field(max_length=10)
    slug: str = Field(max_length=64)
    recall_id: str = Field(max_length=20)


class UnitGuideRequest(BaseModel):
    kind: str = Field(pattern="^(vehicle|product)$")
    slug: str = Field(min_length=1, max_length=64)
    recall_id: str = Field(min_length=3, max_length=15)


class UnitCheckRequest(UnitGuideRequest):
    answer: str = Field(default="", max_length=500)          # what the label says, or a VIN-check answer id
    photo: str = Field(default="", max_length=9_000_000)     # data:image/...;base64 (shrunk in the browser first)


class ReceiptsIn(BaseModel):
    text: str = Field(default="", max_length=30_000)        # order emails, receipts, an order-history file's names
    photo: str = Field(default="", max_length=9_000_000)    # a photo of a paper receipt (data URL, shrunk first)


class BuyingText(BaseModel):
    text: str = Field(min_length=1, max_length=2000)   # what they're looking at, or a listing link


class ThingsText(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ProductCreate(BaseModel):
    owner: str = Field(default="", max_length=40)
    product: str = Field(min_length=1, max_length=80)
    brand: str = Field(default="", max_length=60)
    model: str = Field(default="", max_length=60)


class RecallStatusUpdate(BaseModel):
    kind: str = Field(pattern="^(vehicle|product)$")
    slug: str = Field(min_length=1, max_length=64)
    recall_id: str = Field(min_length=3, max_length=15)
    status: str = Field(pattern="^(open|" + "|".join(RECALL_STATUSES) + ")$")


class ProgressUpdate(BaseModel):
    kind: str = Field(pattern="^(vehicle|product)$")
    slug: str = Field(min_length=1, max_length=64)
    recall_ids: list[str] = Field(min_length=1, max_length=20)
    step: str = Field(pattern="^(|contacted|booked)$")      # "" clears it


class SkillSwitch(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    on: bool


class PushSubscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: dict[str, str] = Field(default_factory=dict)


class FixRequest(BaseModel):
    kind: str = Field(pattern="^(vehicle|product)$")
    slug: str = Field(min_length=1, max_length=64)
    location: str = Field(default="", max_length=80)


class MemoryText(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class MeResponse(BaseModel):
    user_id: str
    kind: str
    expires_ts: float | None
    ultra_left_today: int | None  # None = no daily limit
    messages_left_today: int | None = None
    display_name: str | None = None


def create_app(loop: AgentLoop | None = None, settings: Settings | None = None, watch: RecallWatch | None = None,
               push: Push | None = None, search: WebSearch | None = None,
               makers: MakerPages | None = None, units: UnitCheck | None = None,
               reports: OwnerReports | None = None, precheck: PreCheck | None = None,
               receipts: ReceiptReader | None = None) -> FastAPI:
    _setup_logging()
    settings = settings or load_settings()
    settings.require_secret_key()
    memory = MemoryStore(settings.data_dir)
    skills = Skills(memory)
    site_quota = SiteQuota(settings.data_dir)
    history_store = HistoryStore(settings.data_dir)
    search = search or WebSearch(settings, credit_guard=lambda credits: site_quota.try_consume(
        "tavily", settings.site_tavily_credits_per_day or None, credits))
    if loop is None:
        router = ModelRouter(settings)
        watch = watch or RecallWatch(memory, RecallClient(), router, settings.data_dir)
        makers = makers or MakerPages(search, router, settings.data_dir)
        units = units or UnitCheck(router, settings.data_dir, vision=bool(settings.model_vision))
        receipts = receipts or ReceiptReader(router, vision=bool(settings.model_vision))
        reports = reports or OwnerReports(watch.client, router, settings.data_dir)
        precheck = PreCheck(watch, router, search, reports)
        tools = [skills.guard_tool(t) for t in
                 make_recall_tools(memory, watch) + [make_ask_maker_tool(memory, makers, watch),
                                                     make_precheck_tool(precheck)]
                 + builtin_tools(settings, search)]   # a tool whose skill the user turned off says so

        def thing_named(user_id: str, text: str) -> str | None:
            item = MakerPages.find(memory, user_id, text)
            return item.meta.get("name", item.slug) if item else None
        # Thinking off for the chat model: 2-10x faster in a live comparison and still used the calculator
        # for arithmetic. The planner reads each message up front, saves the things the user says they own and
        # runs the recall check itself: with thinking off the chat model often skipped tools and answered from
        # memory, and recall facts must come from the official lists.
        loop = AgentLoop(router, ToolRegistry(settings.data_dir, tools),
                         profile_context=memory.context_for_prompt, plan_thinking=False,
                         planner=RecallPlanner(router, memory.context_for_prompt, thing_named),
                         answer_tier=Tier.BALANCED)

    if reports is None and watch is not None:
        reports = OwnerReports(watch.client, watch.router, settings.data_dir)
    if precheck is None and watch is not None:
        precheck = PreCheck(watch, watch.router, search, reports)
    if push is None and settings.vapid_private_key:
        push = Push(settings.data_dir, settings.vapid_private_key,
                    settings.public_base_url or "https://github.com/lambdabypi/lantern-ai")
    accounts = AccountStore(settings.data_dir, guest_ttl_days=settings.guest_ttl_days)
    signer = SessionSigner(settings.app_secret_key)
    quota = DailyQuota(settings.data_dir)
    accounts.cleanup_expired_guests()

    app = FastAPI(title="Lantern")
    app.add_middleware(GZipMiddleware, minimum_size=1024)  # the page is ~120 KB of HTML; ~30 KB compressed

    @app.exception_handler(AuthError)
    def auth_error(_: Request, e: AuthError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=e.status)

    @app.exception_handler(RecallError)
    def recall_error(_: Request, e: RecallError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(PushError)
    def push_error(_: Request, e: PushError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(UnitCheckError)
    def unit_error(_: Request, e: UnitCheckError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(SkillOff)
    def skill_off(_: Request, e: SkillOff) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=403)

    @app.exception_handler(PhotoError)
    def photo_error(_: Request, e: PhotoError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(ReceiptError)
    def receipt_error(_: Request, e: ReceiptError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=502)

    @app.exception_handler(PreCheckError)
    def precheck_error(_: Request, e: PreCheckError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=502)

    @app.exception_handler(MemoryStoreError)
    def memory_error(_: Request, e: MemoryStoreError) -> JSONResponse:
        return JSONResponse({"detail": str(e)}, status_code=422)

    # ---- identity ---------------------------------------------------------------------------
    def current_account(request: Request) -> Account:
        """Bearer token (personal access token) or session cookie (browser). 401 otherwise."""
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
        cap = message_cap(account)
        messages_left = None if cap is None else max(0, cap - quota.used(account.user_id, "messages"))
        return MeResponse(user_id=account.user_id, kind=account.kind, expires_ts=account.expires_ts,
                          ultra_left_today=left,
                          display_name=account.display_name, messages_left_today=messages_left)

    def message_cap(account: Account) -> int | None:
        cap = settings.guest_messages_per_day if account.is_guest else settings.user_messages_per_day
        return cap or None

    # ---- pages ------------------------------------------------------------------------------
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    # ---- installable app (PWA): manifest, service worker, icons --------------------------------
    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript",
                            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

    @app.get("/icons/{name}", include_in_schema=False)
    def icon(name: str) -> FileResponse:
        if name not in ICONS:
            raise HTTPException(status_code=404, detail="Not found.")
        return FileResponse(WEB_DIR / "icons" / name, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/fonts/{name}", include_in_schema=False)
    def font(name: str) -> FileResponse:
        if name not in FONTS:
            raise HTTPException(status_code=404, detail="Not found.")
        kind = "font/woff2" if name.endswith(".woff2") else "text/plain; charset=utf-8"
        return FileResponse(WEB_DIR / "fonts" / name, media_type=kind, headers={"Cache-Control": "public, max-age=2592000"})

    # ---- auth -------------------------------------------------------------------------------
    @app.post("/api/auth/guest", response_model=MeResponse)
    def guest(request: Request, response: Response) -> MeResponse:
        # Per-network limit, so per-guest message caps can't be dodged by making endless guests (#6).
        # Only a keyed hash of the address is stored, never the address itself.
        network = hmac.new(settings.app_secret_key.encode(), client_ip(request, settings.trusted_proxy_hops).encode(),
                           "sha256").hexdigest()[:16]
        # Entry count only (no addresses): shows how many proxies append to the header, i.e. TRUSTED_PROXY_HOPS.
        log.info("guest sign-up; X-Forwarded-For entries: %d",
                 len([e for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()]))
        if not site_quota.try_consume(f"guests:{network}", settings.guests_per_ip_per_day or None):
            raise AuthError("Too many guest sessions from your network today. Create an account, or try again "
                            "tomorrow.", status=429)
        return start_session(response, accounts.create_guest())

    @app.post("/api/auth/register", response_model=MeResponse)
    def register(creds: Credentials, response: Response) -> MeResponse:
        return start_session(response, accounts.register(creds.username, creds.password))

    @app.post("/api/auth/keep", response_model=MeResponse)
    def keep_data(creds: Credentials, response: Response, account: Account = Depends(current_account)) -> MeResponse:
        """A guest saves their list: becomes a registered account with the same data."""
        return start_session(response, accounts.keep_guest_data(account, creds.username, creds.password))

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

    # ---- personal access tokens (for scripts and other clients) ---------------------------------
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
        """Daily limits that protect the demo's Token Factory and Tavily credits. Checked before any model call."""
        def blocked(text: str, tier: str = "fast") -> ChatResponse:
            return ChatResponse(answer=text, stop_reason="quota", tool_calls=[], tokens=0, model="", tier=tier)

        if req.escalate and account.is_guest and not quota.try_consume(
                account.user_id, "ultra", settings.guest_ultra_per_day):
            return blocked(f"You've used today's {settings.guest_ultra_per_day} \"Think harder\" turns for guests. "
                           "Untick it to keep chatting with the fast model, or create an account.", "reasoning")
        cap = message_cap(account)
        if not quota.try_consume(account.user_id, "messages", cap):
            more = " Create an account for more, or" if account.is_guest else ""
            return blocked(f"You've reached today's limit of {cap} messages.{more} Come back tomorrow (it resets "
                           "at midnight UTC).")
        if not site_quota.try_consume("messages", settings.site_messages_per_day or None):
            return blocked("Lantern's demo has reached its daily limit across all users. It resets at "
                           "midnight UTC. Sorry!")
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
    def chat(req: ChatRequest, account: Account = Depends(current_account)) -> ChatResponse:
        message = check_message(req)
        if (blocked := quota_block(req, account)) is not None:
            return blocked
        return run_chat_turn(req, account, message)

    @app.post("/api/chat/stream")
    def chat_stream(req: ChatRequest, account: Account = Depends(current_account)) -> StreamingResponse:
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

        return StreamingResponse(events(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/reset")
    def reset(account: Account = Depends(current_account)) -> dict[str, bool]:
        history_store.clear(account.user_id)
        return {"ok": True}

    @app.get("/api/history")
    def history(account: Account = Depends(current_account)) -> list[dict[str, str]]:
        return history_store.recent(account.user_id, MAX_HISTORY_MESSAGES)

    # ---- visible memory (My stuff) ---------------------------------------------
    def check_kind(kind: str) -> str:
        if kind not in (*KINDS, "profile"):
            raise HTTPException(status_code=404, detail="Unknown memory type.")
        return kind

    @app.get("/api/profile")
    def profile(account: Account = Depends(current_account)) -> dict:
        uid = account.user_id
        prof = memory.get(uid, "profile")
        return {"profile": prof.summary() if prof else None,
                "vehicles": [v.summary() for v in memory.list(uid, "vehicle")],
                "products": [p.summary() for p in memory.list(uid, "product")]}

    @app.post("/api/memory/{kind}")
    def create_memory(kind: str, req: MemoryCreate, account: Account = Depends(current_account)) -> dict:
        return memory.upsert(account.user_id, check_kind(kind), req.name, req.fields, note=req.note,
                             source="user").summary()

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

    def active_users_for_cron(request: Request) -> list[str]:
        """Daily jobs (Cloud Scheduler sends X-Lantern-Cron). 404 unless CRON_SECRET is set and matches."""
        sent = request.headers.get("x-lantern-cron", "")
        if not settings.cron_secret or not hmac.compare_digest(sent, settings.cron_secret):
            raise HTTPException(status_code=404, detail="Not found.")
        users_root = settings.data_dir / "users"
        active = [p.name for p in sorted(users_root.iterdir()) if p.is_dir()] if users_root.is_dir() else []
        return [uid for uid in active if accounts.get(uid) is not None]  # skips expired guests

    # ---- the recall watch -------------------------------------------------------------------
    def need_watch() -> RecallWatch:
        if watch is None:
            raise HTTPException(status_code=503, detail="The recall watch isn't available on this server.")
        return watch

    @app.get("/api/recalls")
    def recalls(account: Account = Depends(current_account)) -> dict:
        """Everything that may affect the user's things. Fast: recall data is cached per day, and no model is called
        here; plain summaries not yet written are counted in "plain_pending" (see below)."""
        return need_watch().check(account.user_id, write_plain=False)

    @app.post("/api/recalls/plain")
    def recalls_in_plain_english(account: Account = Depends(current_account)) -> dict:
        """The same report after Nemotron writes the missing plain-English summaries. The page asks for this after
        showing the official wording, so nobody waits for the model before seeing their recalls."""
        return need_watch().check(account.user_id)

    @app.post("/api/vehicles")
    def add_vehicle(req: VehicleCreate, account: Account = Depends(current_account)) -> dict:
        w = need_watch()
        item = w.save_vehicle(account.user_id, req.make, req.model, req.year, req.vin, source="user", owner=req.owner)
        return {"item": item.summary(), "recalls": w.check(account.user_id)}

    @app.get("/api/vehicles/{slug}/owner-reports")
    def owner_reports(slug: str, account: Account = Depends(current_account)) -> dict:
        """What other owners of this make, model and year report to NHTSA, grouped, with whether a recall covers each
        problem. Shared per model and refreshed weekly (see app/owner_reports.py)."""
        w = need_watch()
        if reports is None:
            raise HTTPException(status_code=503, detail="Owner reports aren't available on this server.")
        skills.require(account.user_id, "owner-reports")
        item = memory.get(account.user_id, "vehicle", slug)
        if item is None:
            raise HTTPException(status_code=404, detail="That car isn't in your list.")
        m = item.meta
        model = m.get("nhtsa_model") or m.get("model")
        if not (m.get("make") and model and m.get("year")):
            raise HTTPException(status_code=422, detail="Add the car's make, model and year first.")
        out = reports.for_vehicle(m["make"], model, int(m["year"]), w.vehicle_recalls(m["make"], model, int(m["year"])))
        name = m.get("name", item.slug)
        return {**out, "slug": item.slug, "name": f"{m['owner']}'s {name}" if m.get("owner") else name}

    @app.post("/api/products")
    def add_product(req: ProductCreate, account: Account = Depends(current_account)) -> dict:
        w = need_watch()
        item = w.save_product(account.user_id, req.product, req.brand, req.model, source="user", owner=req.owner)
        return {"item": item.summary(), "recalls": w.check(account.user_id)}

    def find_recall(account: Account, kind: str, slug: str, recall_id: str) -> dict:
        """One recall on the user's list, as the report shows it. 404 if it isn't theirs."""
        report = need_watch().check(account.user_id, write_plain=False)
        item = next((i for i in report["items"] if i["kind"] == kind and i["slug"] == slug), None)
        rc = next((r for r in (item or {}).get("recalls", []) if r["id"].upper() == recall_id.upper()), None)
        if item is None or rc is None:
            raise HTTPException(status_code=404, detail="That recall isn't on your list.")
        return rc

    def spend_message(account: Account, why: str) -> None:
        """Model calls the user asks for outside chat count toward the same daily caps."""
        if not quota.try_consume(account.user_id, "messages", message_cap(account)) or \
                not site_quota.try_consume("messages", settings.site_messages_per_day or None):
            raise HTTPException(status_code=429, detail=f"Today's message limit is used up. {why} come back "
                                                        "tomorrow (it resets at midnight UTC).")

    @app.post("/api/recalls/remedy")
    def remedy(req: RemedyRequest, account: Account = Depends(current_account)) -> dict:
        """How to actually get this recall's free fix, from the maker's own recall page (read with Tavily, steps
        written by Nemotron Super from what the page says). Shared per recall and kept for 14 days."""
        if makers is None:
            raise HTTPException(status_code=503, detail="Reading makers' pages isn't available here.")
        skills.require(account.user_id, "makers-pages")
        rc = find_recall(account, req.kind, req.slug, req.recall_id)
        file = memory.get(account.user_id, req.kind, req.slug)
        try:
            return makers.remedy(req.kind, file.meta if file else {}, rc)
        except MakerError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    # ---- is MY one affected? (see app/unit_check.py) ------------------------------------------
    def need_units() -> UnitCheck:
        if units is None:
            raise HTTPException(status_code=503, detail="Checking your own unit isn't available on this server.")
        return units

    def full_notice(kind: str, rc: dict):
        if kind != "product":
            return None
        try:
            return need_watch().product_notice(rc["id"])
        except RecallError:
            return None  # the start of the description in the report is still checked

    @app.post("/api/recalls/unit/guide")
    def unit_guide(req: UnitGuideRequest, account: Account = Depends(current_account)) -> dict:
        """Where to look on the product (or how to use NHTSA's VIN check) to tell if this recall covers yours."""
        skills.require(account.user_id, "unit-check")
        rc = find_recall(account, req.kind, req.slug, req.recall_id)
        guide = need_units().guide(req.kind, rc, full_notice(req.kind, rc))
        if req.kind == "vehicle":
            file = memory.get(account.user_id, req.kind, req.slug)
            guide["vin"] = (file.meta.get("vin") if file else "") or ""  # their own VIN, to paste into the check
        return guide

    @app.post("/api/recalls/unit/check")
    def unit_verdict(req: UnitCheckRequest, account: Account = Depends(current_account)) -> dict:
        """The owner's label (typed or photographed) or VIN-check result -> affected / not affected / unsure, saved
        in the item's file. The recall's status is left for the owner to change."""
        u = need_units()
        skills.require(account.user_id, "unit-check")
        rc = find_recall(account, req.kind, req.slug, req.recall_id)
        if req.kind == "product":
            spend_message(account, "Compare your label with the official notice yourself, or")
        label = u.read_label(req.photo) if req.photo else ""
        out = u.decide(req.kind, rc, full_notice(req.kind, rc), req.answer, label)
        file = memory.get(account.user_id, req.kind, req.slug)
        if file is not None and out["verdict"] in UNIT_CHECKS:
            checks = {**(file.meta.get("unit_check") or {}), out["recall_id"]: out["verdict"]}
            if label:
                said = " Label: " + " / ".join(label.splitlines())[:200] + "."
            elif req.kind == "product":
                said = " They said: " + req.answer.strip()[:200] + "."
            else:
                said = ""
            memory.upsert(account.user_id, req.kind, file.slug, {"unit_check": checks},
                          note=f"Checked recall {out['recall_id']}: {out['why']}{said}", source="user")
        out["recalls"] = need_watch().check(account.user_id, write_plain=False)
        return out

    @app.post("/api/things")
    def add_things(req: ThingsText, account: Account = Depends(current_account)) -> dict:
        """Everything someone owns in their own words, typed or spoken ("our 2018 Civic, a Graco stroller and a
        crib"). Nemotron Nano picks out each thing, then each is saved and checked. Counts as one message toward
        the daily caps, because it calls the model."""
        w = need_watch()
        reader = getattr(loop, "planner", None)
        if not isinstance(reader, RecallPlanner):
            raise HTTPException(status_code=503, detail="Reading free text isn't available here. Add a car or a product instead.")
        spend_message(account, "Add things one at a time in My stuff, or")
        things = reader.owned(req.text.strip())
        if things is None:
            raise HTTPException(status_code=502, detail="Lantern couldn't read that just now. Try again, or type one "
                                                        "thing, like \"2018 Honda Civic\".")
        if not things:
            raise HTTPException(status_code=422, detail="I couldn't find anything you own in that. Try something like "
                                                        "\"a 2018 Honda Civic, a crib and a space heater\". For a car "
                                                        "I need the year, make and model, or the VIN.")
        saved, problems = save_things(w, account.user_id, things)
        if not saved:
            raise HTTPException(status_code=422, detail=" ".join(problems) or "Nothing could be saved.")
        return {"saved": saved, "problems": problems, "recalls": w.check(account.user_id)}

    @app.post("/api/things/receipts/photo")
    def read_receipt_photo(req: ReceiptsIn, account: Account = Depends(current_account)) -> dict:
        """The item lines of a photographed receipt, as text the person checks (and fixes) before adding anything."""
        if receipts is None:
            raise HTTPException(status_code=503, detail="Reading receipts isn't available on this server.")
        skills.require(account.user_id, "receipts")
        if not req.photo:
            raise HTTPException(status_code=422, detail="Take or choose a photo of the receipt first.")
        spend_message(account, "Paste the order email instead, or")
        return {"text": receipts.photo_text(req.photo)}

    @app.post("/api/things/receipts")
    def add_from_receipts(req: ReceiptsIn, account: Account = Depends(current_account)) -> dict:
        """Everything in pasted order emails, an order-history file or a photo of a receipt (see app/receipts.py).
        Counts as one message toward the daily caps."""
        w = need_watch()
        if receipts is None:
            raise HTTPException(status_code=503, detail="Reading receipts isn't available on this server.")
        skills.require(account.user_id, "receipts")
        if not (req.text.strip() or req.photo):
            raise HTTPException(status_code=422, detail="Paste an order email, choose a file, or take a photo of a receipt.")
        spend_message(account, "Add things one at a time in My stuff, or")
        text = req.text
        if req.photo:
            text = receipts.photo_text(req.photo) + "\n" + text
        things = receipts.things(text)
        if not things:
            raise HTTPException(status_code=422, detail="No products that can be recalled were found in that. Lantern "
                                                        "skips food, medicine, books, gift cards and services.")
        saved, problems = save_things(w, account.user_id, things, limit=MAX_RECEIPT_THINGS)
        if not saved:
            raise HTTPException(status_code=422, detail=" ".join(problems) or "Nothing could be saved.")
        return {"saved": saved, "problems": problems, "recalls": w.check(account.user_id)}

    @app.post("/api/precheck")
    def check_before_buying(req: BuyingText, account: Account = Depends(current_account)) -> dict:
        """Recalls and owner complaints for something secondhand, before buying it (see app/precheck.py). Nothing is
        added to the list. Counts as one message toward the daily caps."""
        if precheck is None:
            raise HTTPException(status_code=503, detail="Checking before you buy isn't available on this server.")
        skills.require(account.user_id, "check-before-buying")
        spend_message(account, "Add it to My stuff to check it, or")
        things, listing = precheck.read(req.text.strip())
        if not things:
            note = (listing or {}).get("note")
            raise HTTPException(status_code=422, detail=note or "I couldn't tell what's for sale. Try something like "
                                                                "\"a used 2014 Jeep Cherokee\" or \"a Graco high chair\". For a car "
                                                                "I need the year, make and model, or the VIN.")
        results = precheck.check(things)
        if not skills.on(account.user_id, "owner-reports"):
            for r in results:
                r.pop("owner_reports", None)
        return {"results": results, "listing": listing}

    @app.post("/api/demo")
    def demo(account: Account = Depends(current_account)) -> dict:
        """Fill an empty account with a made-up household (real recall data, no real people)."""
        return load_demo_household(memory, need_watch(), account.user_id)

    @app.post("/api/fix")
    def book_the_fix(req: FixRequest, account: Account = Depends(current_account)) -> dict:
        """Who to contact and a ready-to-send message. Nothing is sent: the user sends it from their own app."""
        uid = account.user_id
        skills.require(uid, "book-the-fix")
        item = memory.get(uid, req.kind, req.slug)
        if item is None:
            raise HTTPException(status_code=404, detail="That item isn't in your list.")
        report = need_watch().check(uid)
        entry = next((i for i in report["items"] if i["kind"] == req.kind and i["slug"] == req.slug), None)
        recalls = [r for r in (entry["recalls"] if entry else []) if r["status"] == "open"]
        if not recalls:
            raise HTTPException(status_code=422, detail="This item has no open recalls to fix.")
        out: dict = {"draft": draft_message(req.kind, item.meta, recalls), "dealers": [], "maps_url": None,
                     "contact": None, "note": None}
        if req.kind == "product":
            out["contact"] = next((r.get("contact") for r in recalls if r.get("contact")), None)
            return out
        location = req.location.strip()
        if location:
            memory.upsert(uid, "profile", None, {"location": location}, source="user")
        else:
            prof = memory.get(uid, "profile")
            location = (prof.meta.get("location") if prof else "") or ""
        out["location"] = location
        if location:
            out.update(find_dealers(search, item.meta.get("make", ""), location))
        return out

    @app.post("/api/recalls/status")
    def recall_status(req: RecallStatusUpdate, account: Account = Depends(current_account)) -> dict:
        """Only the user marks recalls fixed or not theirs: the assistant has no tool for it."""
        w = need_watch()
        w.set_status(account.user_id, req.kind, req.slug, req.recall_id, req.status)
        return w.check(account.user_id)

    @app.post("/api/recalls/progress")
    def recall_progress(req: ProgressUpdate, account: Account = Depends(current_account)) -> dict:
        """The user says they contacted the maker or dealer, or booked the repair. The daily watch then checks in
        (see app/followups.py). Only the user records this: the assistant has no tool for it."""
        w = need_watch()
        for rid in req.recall_ids:
            find_recall(account, req.kind, req.slug, rid)          # 404 unless it's one of their recalls
            w.set_progress(account.user_id, req.kind, req.slug, rid, req.step)
        return w.check(account.user_id, write_plain=False)

    @app.post("/internal/recalls/run", include_in_schema=False)
    def run_recall_watch(request: Request) -> dict:
        alerts = daily_alerts(push, FollowUps(settings.data_dir, push), skills) if push else None
        result = need_watch().run_all(active_users_for_cron(request), alerts)
        log.info("recall watch run: %s", result)
        return result

    # ---- push notifications ("Alerts on this device") --------------------------------------
    def need_push() -> Push:
        if push is None:
            raise HTTPException(status_code=503, detail="Notifications aren't set up on this server.")
        return push

    @app.get("/api/push")
    def push_info(account: Account = Depends(current_account)) -> dict:
        if push is None:
            return {"available": False, "enabled": False, "public_key": None}
        return {"available": True, "enabled": push.enabled_for(account.user_id), "public_key": push.public_key}

    @app.post("/api/push/subscribe")
    def push_subscribe(req: PushSubscription, account: Account = Depends(current_account)) -> dict:
        p = need_push()
        p.subscribe(account.user_id, req.model_dump())
        sent = p.send(account.user_id, "Lantern alerts are on",
                      "You'll get a notification here when a new recall affects your things.", "/#attention")
        return {"ok": True, "test_sent": sent}

    @app.post("/api/push/unsubscribe")
    def push_unsubscribe(req: PushSubscription, account: Account = Depends(current_account)) -> dict:
        need_push().unsubscribe(account.user_id, req.endpoint)
        return {"ok": True}

    # ---- skills: each capability has a SKILL.md, and the user turns them on or off (app/skill_files.py) --------
    @app.get("/api/skills")
    def list_skills(account: Account = Depends(current_account)) -> list[dict]:
        return skills.listing(account.user_id)

    @app.post("/api/skills")
    def switch_skill(req: SkillSwitch, account: Account = Depends(current_account)) -> list[dict]:
        if req.id not in skills.all:
            raise HTTPException(status_code=404, detail="No such skill.")
        return skills.set(account.user_id, req.id, req.on)

    @app.get("/api/skills/{skill_id}")
    def read_skill(skill_id: str, account: Account = Depends(current_account)) -> dict:
        skill = skills.all.get(skill_id)
        if skill is None:
            raise HTTPException(status_code=404, detail="No such skill.")
        return {"id": skill.id, "text": skill.text}

    @app.get("/api/export")
    def export(account: Account = Depends(current_account)) -> RawResponse:
        return RawResponse(memory.export_zip(account.user_id), media_type="application/zip",
                           headers={"Content-Disposition": 'attachment; filename="my-lantern-data.zip"'})

    return app


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def client_ip(request: Request, trusted_hops: int) -> str:
    """The visitor's address. Behind a proxy, use the entry the proxy appended to X-Forwarded-For (the
    rightmost `trusted_hops` entries); never the leftmost, which the visitor can set to anything."""
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    entries = [e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()]
    return entries[-trusted_hops] if len(entries) >= trusted_hops else peer
