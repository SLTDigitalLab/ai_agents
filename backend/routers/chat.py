"""
Chat router - connects the React frontend to the LangGraph agent system.

POST /api/v1/chat  →  Compiles the agent graph on-the-fly with a per-request
database connection to ensure clean resource cleanup.
"""

from fastapi import APIRouter, HTTPException

from core.checkpointer import get_postgres_checkpointer
from domain.registry import get_agent_builder
from schemas.chat import ChatRequest
from langchain_core.tracers.context import tracing_v2_enabled

# Removed trailing slash from prefix if it acts as base
router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post("")  # Mounts at /api/v1/chat (no trailing slash)
async def chat(request: ChatRequest):
    """Handle an incoming chat message from the frontend."""
    try:
        print(f"DEBUG: Received agent_id: {request.agent_id}")
        builder_fn = get_agent_builder(request.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Build the uncompiled workflow
    workflow = builder_fn()

    # Thread config enables LangGraph memory/checkpointing per conversation
    config = {"configurable": {"thread_id": request.thread_id}}

    try:
        # ── The "With" Pattern ───────────────────────────────────────────────
        # We open the DB connection pool & checkpointer HERE, use it, and
        # let it close automatically when the block exits.
        with get_postgres_checkpointer(request.agent_id) as checkpointer:
            
            # Compile the graph with this specific checkpointer instance
            graph = workflow.compile(checkpointer=checkpointer)

            # Invoke using the standard LangGraph arguments
            # Note: We must await if using async, but standard invoke is sync.
            # If using asyncpg, we'd need a different pattern.
            # psycopg 3 sync connection is fine for now.
            state = {
                "messages": [("user", request.message)],
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "form_slots": {},
                "next_node": "",
            }

            # Wrap the invocation to dynamically separate traces
            project_name = f"Ask SLT - {request.agent_id.upper()}"
            with tracing_v2_enabled(project_name=project_name):
                result = graph.invoke(state, config=config)

            # Extract the final message
            final_message = result["messages"][-1].content

            # If Gemini returns a list of blocks (common after tool calls)
            if isinstance(final_message, list):
                text_parts = []
                for block in final_message:
                    if isinstance(block, str):
                        text_parts.append(block)
                    elif isinstance(block, dict) and "text" in block:
                        text_parts.append(block["text"])
                final_message = " ".join(text_parts)
            elif not isinstance(final_message, str):
                # Fallback for any other object type
                final_message = str(final_message)

            return {"response": final_message}

    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"Agent execution failed: {exc}",
        )


@router.get("/{agent_id}/{thread_id}")
async def get_history(agent_id: str, thread_id: str):
    """Retrieve the chat history for a specific session."""
    try:
        builder_fn = get_agent_builder(agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    workflow = builder_fn()
    config = {"configurable": {"thread_id": thread_id}}

    try:
        with get_postgres_checkpointer(agent_id) as checkpointer:
            graph = workflow.compile(checkpointer=checkpointer)
            
            # Get the current state snapshot from the database
            snapshot = graph.get_state(config)
            
            if not snapshot.values:
                return {"messages": []}

            # return a simplified list of messages
            # snapshot.values['messages'] is a list of LangChain objects
            messages = []
            for msg in snapshot.values.get("messages", []):
                # Only expose Human and AI messages to the frontend
                if msg.type not in ("human", "ai"):
                    continue

                content = msg.content
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, str):
                            text_parts.append(block)
                        elif isinstance(block, dict) and "text" in block:
                            text_parts.append(block["text"])
                    content = " ".join(text_parts).strip()
                elif not isinstance(content, str):
                    content = str(content).strip()
                
                # Skip empty messages (e.g., AI messages that only performed a tool call but had no text)
                if content:
                    messages.append({
                        "type": msg.type,
                        "content": content
                    })
            
            return {"messages": messages}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
