from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import marginalia.tasks.handlers  # noqa: F401  (registers task handlers)
from marginalia.api.routes_agent import router as sessions_router
from marginalia.api.routes_chat import router as chat_router
from marginalia.api.routes_exports import router as exports_router
from marginalia.api.routes_file_entries import router as file_entries_router
from marginalia.api.routes_folders import router as folders_router
from marginalia.api.routes_tasks import router as tasks_router
from marginalia.api.routes_tend import router as tend_router
from marginalia.api.routes_upload import router as upload_router
from marginalia.api.routes_user_files import router as user_files_router
from marginalia.config import get_settings
from marginalia.db.engine import dispose_engine
from marginalia.tasks.runner import TaskRunner

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    runner: TaskRunner | None = None
    if settings.worker_enabled:
        runner = TaskRunner(settings)
        await runner.start()
        log.info("task runner started in-process")
    try:
        yield
    finally:
        if runner is not None:
            await runner.stop()
        await dispose_engine()


app = FastAPI(title="Marginalia", lifespan=lifespan)

V1_PREFIX = "/v1"
app.include_router(folders_router, prefix=V1_PREFIX)
app.include_router(file_entries_router, prefix=V1_PREFIX)
app.include_router(upload_router, prefix=V1_PREFIX)
app.include_router(user_files_router, prefix=V1_PREFIX)
app.include_router(sessions_router, prefix=V1_PREFIX)
app.include_router(chat_router, prefix=V1_PREFIX)
app.include_router(exports_router, prefix=V1_PREFIX)
app.include_router(tasks_router, prefix=V1_PREFIX)
app.include_router(tend_router, prefix=V1_PREFIX)


@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
