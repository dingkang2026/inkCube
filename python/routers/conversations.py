import logging

from fastapi import APIRouter

import deps

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/conversations")
async def list_conversations():
    return deps.db.list_conversations()


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    messages = deps.db.get_messages(conversation_id)
    return {"messages": messages}


@router.post("/conversations")
async def create_conversation():
    logger.info("New conversation created")
    return deps.db.create_conversation()


@router.patch("/conversations/{conversation_id}")
async def rename_conversation(conversation_id: str, body: dict):
    logger.info("Conversation renamed: id=%s, title=%s", conversation_id, body.get("title", "")[:50])
    deps.db.rename_conversation(conversation_id, body["title"])
    return {"ok": True}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    logger.info("Conversation deleted: id=%s", conversation_id)
    deps.db.delete_conversation(conversation_id)
    return {"ok": True}
