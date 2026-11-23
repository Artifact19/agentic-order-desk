import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import MAX_MESSAGE_LENGTH, SCREENS_DIR, STATIC_DIR
from .faults import FAULT_MODES
from .service import AgentService, ServiceError


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    address: Optional[str] = Field(default=None, max_length=200)


class ActionIn(BaseModel):
    action: str = Field(pattern="^(approve|next|cancel)$")


class FaultIn(BaseModel):
    step: str = Field(min_length=1, max_length=80)
    mode: str = Field(pattern="^(" + "|".join(FAULT_MODES) + ")$")


class DelayIn(BaseModel):
    seconds: float = Field(ge=0, le=5)


def create_app(service_factory=AgentService) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service_factory()
        yield
        app.state.service.shutdown()

    app = FastAPI(title="AI Agent for Online Food Ordering", lifespan=lifespan)

    def service(request: Request) -> AgentService:
        return request.app.state.service

    @app.middleware("http")
    async def revalidate_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path in ("/", "/admin"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, error: ServiceError):
        return JSONResponse({"error": str(error)}, status_code=error.status)

    @app.get("/api/health")
    def health(request: Request):
        s = service(request)
        return {"status": "ok", "llm": s.llm_status, "embeddings": s.embedder_status}

    @app.post("/api/sessions")
    def create_session(request: Request):
        return service(request).create_session()

    @app.get("/api/sessions")
    def list_sessions(request: Request):
        return service(request).session_repo.list_sessions()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, request: Request):
        return service(request).session_view(session_id)

    @app.post("/api/sessions/{session_id}/clear")
    def clear_session(session_id: str, request: Request):
        return service(request).clear_session(session_id)

    @app.post("/api/sessions/{session_id}/messages")
    def post_message(session_id: str, body: MessageIn, request: Request):
        return service(request).handle_message(session_id, body.text, body.address)

    @app.post("/api/sessions/{session_id}/actions")
    def post_action(session_id: str, body: ActionIn, request: Request,
                    idempotency_key: Optional[str] = Header(default=None, max_length=100)):
        return service(request).act(session_id, body.action, idempotency_key)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request):
        return service(request).run_view(run_id)

    @app.post("/api/runs/{run_id}/resume")
    def resume_run(run_id: str, request: Request):
        return service(request).resume_run(run_id)

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: str, request: Request, since: int = 0):
        bus = service(request).bus
        if not bus.has(run_id):
            raise ServiceError("No live event stream for this run", 404)

        def stream():
            for event in bus.listen(run_id, since):
                yield ": keep-alive\n\n" if event is None else f"data: {json.dumps(event, default=str)}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/system")
    def system(request: Request):
        return service(request).system_view()

    @app.post("/api/cache/flush")
    def flush_cache(request: Request):
        return service(request).flush_data_caches()

    @app.get("/api/faults")
    def faults(request: Request):
        return service(request).faults.describe()

    @app.post("/api/faults")
    def arm_fault(body: FaultIn, request: Request):
        return service(request).faults.arm(body.step, body.mode)

    @app.delete("/api/faults")
    def clear_faults(request: Request):
        service(request).faults.disarm()
        return service(request).faults.describe()

    @app.post("/api/faults/delay")
    def set_delay(body: DelayIn, request: Request):
        service(request).faults.step_delay_seconds = body.seconds
        return service(request).faults.describe()

    @app.get("/api/observations")
    def observations(request: Request):
        return service(request).observations_view()

    @app.get("/api/faults/steps")
    def fault_steps(request: Request):
        return service(request).fault_steps()

    @app.get("/api/memory")
    def memory(request: Request):
        return service(request).memory_view()

    @app.get("/api/memory/search")
    def memory_search(q: str, request: Request):
        return service(request).search_memory(q[:200])

    @app.delete("/api/memory")
    def reset_memory(request: Request):
        return service(request).reset_memory()

    @app.get("/api/zomato/status")
    def zomato_status(request: Request, refresh: bool = False):
        return service(request).zomato_status(refresh)

    @app.post("/api/zomato/connect")
    def zomato_connect(request: Request):
        return service(request).zomato_connect()

    @app.post("/api/zomato/skip-login")
    def zomato_skip(request: Request):
        service(request).zomato_skip_login = True
        return {"skipped": True}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/screens", StaticFiles(directory=SCREENS_DIR), name="screens")

    @app.get("/")
    def customer_page():
        return FileResponse(STATIC_DIR / "customer.html")

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC_DIR / "admin.html")

    return app


app = create_app()
