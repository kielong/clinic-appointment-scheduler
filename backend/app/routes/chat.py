import json
import logging

from fastapi import APIRouter, Depends
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent import pending
from app.agent.agent import build_agent
from app.agent.tools import Grounding, final_reply
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import ChatMessage, ChatRequest
from app.services.scheduling import SchedulingError

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)

# Safety net so a misbehaving model can't loop on tool calls forever.
RECURSION_LIMIT = 12


def _to_lc_message(msg: ChatMessage):
    if msg.role == "user":
        return HumanMessage(content=msg.content)
    return AIMessage(content=msg.content)


def _event(kind: str, **data) -> str:
    return json.dumps({"type": kind, **data})


def _reply(text: str):
    """Stream a complete server-authored reply."""

    async def stream():
        yield _event("token", content=text)
        yield _event("done")

    return EventSourceResponse(stream())


@router.post("")
async def chat(
    body: ChatRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stream a response from the scheduling agent via Server-Sent Events.

    Confirmation of a proposed booking/cancellation/reschedule is handled here, not by the
    model: if the user has a pending action and replies "yes", it is executed directly.
    """
    action = await pending.get_pending(db, user.id)
    if action:
        if pending.is_confirmation(body.message):
            try:
                message = await pending.execute(db, user, action)
            except SchedulingError as e:
                message = f"Sorry, I couldn't complete that: {e.message}"
            logger.info("[chat.confirmed] user=%s kind=%s -> %s", user.id, action.kind, message)
            return _reply(message)
        await pending.clear(db, user.id)
        if pending.is_rejection(body.message):
            return _reply(
                "Okay, I haven't made any changes. Is there anything else I can help with?"
            )

    grounding = Grounding(
        latest=body.message,
        earlier=[m.content for m in body.history if m.role == "user"][-6:],
    )
    agent = build_agent(user, grounding=grounding)
    history = [_to_lc_message(m) for m in body.history if m.content.strip()]
    messages = [*history, HumanMessage(content=body.message)]

    async def event_stream():
        try:
            async for event in agent.astream_events(
                {"messages": messages},
                version="v2",
                config={"recursion_limit": RECURSION_LIMIT},
            ):
                kind = event["event"]
                if kind == "on_tool_start":
                    logger.info(
                        "[chat.tool_start] user=%s name=%s input=%s",
                        user.id,
                        event.get("name", "unknown"),
                        event.get("data", {}).get("input", {}),
                    )
                    yield _event("reset")
                elif kind == "on_tool_error":
                    logger.error(
                        "[chat.tool_error] name=%s error=%s",
                        event.get("name", "unknown"),
                        event.get("data", {}).get("error", {}),
                    )
                elif kind == "on_tool_end":
                    output = event.get("data", {}).get("output", "")
                    reply = final_reply(output)
                    if reply:
                        # Confirmations and write errors come from the server verbatim, so what
                        # the user approves is exactly what will be executed.
                        yield _event("reset")
                        yield _event("token", content=reply)
                        yield _event("done")
                        return
                elif kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if chunk.content:
                        yield _event("token", content=chunk.content)
            yield _event("done")
        except Exception as e:
            logger.exception("[chat.error] %s", e)
            yield _event(
                "error", error="The assistant is unavailable right now. Please try again shortly."
            )

    return EventSourceResponse(event_stream())
