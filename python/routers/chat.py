import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

import deps
from schemas import ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest):
    logger.info("Chat request: conv_id=%s, message_len=%d", req.conversation_id, len(req.message))
    deps.db.save_message(req.conversation_id, "user", req.message)
    deps.db.update_conversation_title_from_message(req.conversation_id, req.message)

    async def event_stream():
        logger.debug("Starting chat event stream: %s", req.conversation_id)
        async for event in deps.agent.run(req.conversation_id, req.message):
            yield f"event: {event['type']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
        logger.debug("Chat event stream completed: %s", req.conversation_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/chat/cancel")
async def cancel_chat():
    logger.info("Chat cancelled by user")
    deps.agent.request_cancel()
    return {"ok": True}
