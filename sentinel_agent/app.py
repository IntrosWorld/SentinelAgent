from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from sentinel_agent.agent import SentinelAgent
from sentinel_agent.config import get_settings
from sentinel_agent.llm import planner_from_profile
from sentinel_agent.models import ModelRegistry
from sentinel_agent.planner import HybridPlanner
from sentinel_agent.schemas import AgentRun, ApprovalRequest, RunRequest

settings = get_settings()
registry = ModelRegistry(settings.models_file)
model = planner_from_profile(registry.get())
agent = SentinelAgent(HybridPlanner(model), settings.max_steps)
@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        agent.close()


app = FastAPI(title="SentinelAgent", version="0.1.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(static_dir / "index.html")


@app.get("/styles.css", include_in_schema=False)
async def styles() -> FileResponse:
    return FileResponse(static_dir / "styles.css", media_type="text/css")


@app.get("/app.js", include_in_schema=False)
async def javascript() -> FileResponse:
    return FileResponse(static_dir / "app.js", media_type="application/javascript")


@app.get("/api/health")
async def health() -> dict:
    return {"service": "ok", "model": await model.health(), "safe_mode": not settings.allow_real_browser}


@app.post("/api/runs", response_model=AgentRun)
async def start_run(request: RunRequest, disturbance: str | None = Query(default=None)) -> AgentRun:
    return await agent.start(request.instruction, request.use_model, disturbance, False)


@app.get("/api/runs/{run_id}", response_model=AgentRun)
async def get_run(run_id: str) -> AgentRun:
    if run_id not in agent.runs:
        raise HTTPException(404, "Run not found")
    return agent.runs[run_id]


@app.post("/api/runs/{run_id}/approval", response_model=AgentRun)
async def approve_run(run_id: str, request: ApprovalRequest) -> AgentRun:
    if run_id not in agent.runs:
        raise HTTPException(404, "Run not found")
    try:
        return agent.approve(run_id, request.approved)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def run() -> None:
    import uvicorn

    uvicorn.run("sentinel_agent.app:app", host="127.0.0.1", port=8000, reload=True)
