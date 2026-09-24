"""Minimal Channel Gateway -> LangGraph callback service.

This example intentionally uses a deterministic echo node instead of a real LLM.
Replace `agent_node` with your model/tool graph while keeping the HTTP contract.
"""

from __future__ import annotations

import asyncio
import hmac
import os
from copy import deepcopy
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import BaseModel, ConfigDict, Field


CALLBACK_TOKEN = os.environ.get("AGENT_CALLBACK_TOKEN", "")


class CallbackPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    version: str = "1.0"
    type: str
    event: dict[str, Any]


class CallbackMessage(BaseModel):
    text: str
    idempotencyKey: str | None = None


class CallbackResponse(BaseModel):
    ack: bool = True
    messages: list[CallbackMessage] = Field(default_factory=list)


def agent_node(state: MessagesState) -> dict[str, list[AIMessage]]:
    """Replace this node with your LLM/tool workflow."""
    latest_user = next(
        (message.content for message in reversed(state["messages"]) if isinstance(message, HumanMessage)),
        "",
    )
    return {"messages": [AIMessage(content=f"LangGraph received: {latest_user}")]}


builder = StateGraph(MessagesState)
builder.add_node("agent", agent_node)
builder.add_edge(START, "agent")
builder.add_edge("agent", END)
graph = builder.compile(checkpointer=InMemorySaver())

app = FastAPI(title="Channel Gateway LangGraph callback example")

# Demonstration-only idempotency cache. Use a durable database in production.
_processed: dict[str, dict[str, Any]] = {}
_processed_lock = asyncio.Lock()


def require_bearer(authorization: str | None) -> None:
    if not CALLBACK_TOKEN:
        raise HTTPException(status_code=503, detail="AGENT_CALLBACK_TOKEN is not configured")
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
    if not hmac.compare_digest(CALLBACK_TOKEN, supplied):
        raise HTTPException(status_code=401, detail="invalid callback token")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/channel-events", response_model=CallbackResponse)
async def channel_events(
    payload: CallbackPayload,
    authorization: str | None = Header(default=None),
) -> CallbackResponse:
    require_bearer(authorization)
    if payload.type != "channel.inbound":
        raise HTTPException(status_code=400, detail="unsupported callback type")

    event = payload.event
    event_id = str(event.get("id", ""))
    session_key = str((event.get("session") or {}).get("key", ""))
    message = event.get("message") or {}
    text = message.get("text")

    if not event_id or not session_key:
        raise HTTPException(status_code=400, detail="event.id and event.session.key are required")

    async with _processed_lock:
        cached = _processed.get(event_id)
        if cached is not None:
            return CallbackResponse.model_validate(deepcopy(cached))

    if not isinstance(text, str) or not text.strip():
        response = CallbackResponse(ack=True, messages=[])
    else:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=text)]},
            {"configurable": {"thread_id": session_key}},
        )
        reply = next(
            (
                item.content
                for item in reversed(result.get("messages", []))
                if isinstance(item, AIMessage) and isinstance(item.content, str)
            ),
            "",
        )
        response = CallbackResponse(
            ack=True,
            messages=[
                CallbackMessage(
                    text=reply,
                    idempotencyKey=f"langgraph:{event_id}:0",
                )
            ] if reply else [],
        )

    async with _processed_lock:
        _processed[event_id] = response.model_dump()
    return response
