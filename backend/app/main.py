"""FastAPI app. Run from backend/: uvicorn app.main:app --reload --port 8000"""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import advisor, auth, courses, errors, health, messages, schedules, students, terms

log = logging.getLogger("graydi")


def _warm_embedding_model() -> None:
    try:
        from app.tools.vector_search import get_embedding_model

        get_embedding_model()
    except Exception:  # the app still works: the model is then loaded on first use
        log.exception("Could not pre-load the embedding model")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Load the embedding model in the background at startup so the first interest Keep / resume upload is not slow.
    # A request that comes in meanwhile waits for this same load (see get_embedding_model).
    threading.Thread(target=_warm_embedding_model, name="embedding-warmup", daemon=True).start()
    yield


app = FastAPI(title="Gray DI Degree Pathway Advisor", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(terms.router)
app.include_router(students.router)
app.include_router(schedules.router)
app.include_router(courses.router)
app.include_router(advisor.router)
app.include_router(messages.router)
errors.register(app)
