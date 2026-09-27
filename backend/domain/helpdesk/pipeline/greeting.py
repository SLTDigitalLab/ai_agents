"""
Terminal branch for small talk (message_type == "greeting").
"""

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import llm, _message_to_text, _latest_user_message
from domain.helpdesk.prompts import GREETING_SYSTEM_PROMPT


# [NODE] Terminal branch for small talk. One LLM call → END. No follow-up
# phase is set, so the next message always starts a fresh classify_message run.
async def greeting_agent(state: AgentState) -> dict:
    """Handles greeting messages."""
    user_message = _latest_user_message(state)
    user_id = state.get("user_id", "anonymous")

    messages = [
        {"role": "system", "content": GREETING_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    response = await llm.ainvoke(messages)
    reply = _message_to_text(response)
    print(f"[greeting_agent] user_id={user_id!r} | reply={reply!r}")
    return {"messages": [AIMessage(content=reply)]}
