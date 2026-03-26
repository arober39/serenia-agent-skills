"""FastAPI server for the Serenia agent UI."""

import uuid

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from serenia.observability.tracing import init_tracing
from serenia.flags import init_launchdarkly, shutdown as shutdown_ld
from serenia.agent import process_message, get_skill_registry_info

# Initialize on startup
tracer = init_tracing()
ld_client = init_launchdarkly()

app = FastAPI(title="Serenia Agent API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory activity log for the dashboard
activity_log: list[dict] = []


class ChatRequest(BaseModel):
    message: str
    context_key: str | None = None


class ChatResponse(BaseModel):
    response: str
    metadata: dict
    message_id: str


@app.get("/api/skills")
def get_skills():
    """Return the skill registry for the dashboard."""
    return {"skills": get_skill_registry_info()}


@app.get("/api/activity")
def get_activity():
    """Return recent activity for the dashboard."""
    return {"activity": activity_log[-50:]}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Process a customer message through the Serenia agent."""
    context_key = req.context_key or f"web-{uuid.uuid4().hex[:8]}"
    message_id = f"msg-{uuid.uuid4().hex[:8]}"

    result = process_message(req.message, context_key)

    # Add to activity log
    activity_entry = {
        "message_id": message_id,
        "input": req.message[:100],
        **result["metadata"],
    }
    activity_log.append(activity_entry)

    return ChatResponse(
        response=result["response"],
        metadata=result["metadata"],
        message_id=message_id,
    )


@app.on_event("shutdown")
def on_shutdown():
    shutdown_ld()
