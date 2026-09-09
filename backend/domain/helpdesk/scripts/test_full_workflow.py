"""
End-to-end smoke test for the live helpdesk conversational graph — hits the
REAL /api/v1/chat HTTP endpoint (guardrails, Postgres checkpointing,
streaming, the full LangGraph state machine), not individual node
functions, so this exercises exactly what a real user's browser would.

Each "conversation" is a list of user turns sent sequentially under one
thread_id, so multi-turn phases (clarification, category confirmation,
satisfaction check, etc.) are tested the same way a real session would hit
them — via checkpointed state, not hand-built AgentState dicts.

Usage (from /app inside the backend container, backend must be running):
    python domain/helpdesk/scripts/test_full_workflow.py
"""

import asyncio
import time
import uuid

import httpx

BASE_URL = "http://localhost:8000/api/v1/chat"
AGENT_ID = "helpdesk_dev"
USER_ID = "workflow_test_user"


async def send_turn(client: httpx.AsyncClient, thread_id: str, message: str) -> str:
    resp = await client.post(
        BASE_URL,
        json={
            "message": message,
            "agent_id": AGENT_ID,
            "user_id": USER_ID,
            "thread_id": thread_id,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


CONVERSATIONS = [
    {
        "name": "1. Greeting",
        "expect": "Casual greeting reply, no ticket flow started.",
        "turns": ["Hello"],
    },
    {
        "name": "2. Message Analyzer — extremely short, no info",
        "expect": "Clarifying question WITHOUT running the classifier "
        "(1-word message), then a real category draft once detail is given.",
        "turns": [
            "Failed",
            "My fiber router lost internet connection this morning and restarting it didn't help",
        ],
    },
    {
        "name": "3. Direct substantive ticket-worthy issue",
        "expect": "Either an informational KB answer, or (more likely, since "
        "this is specific account data no KB doc would have) a direct ticket "
        "draft with a real category — then keep the category on confirmation.",
        "turns": [
            "My last bill has a data usage charge for a package I never subscribed to, please help me get it reversed",
            "1",
        ],
    },
    {
        "name": "4. Ambiguous SOA/CRM-OM/Clarity-OSS cluster message",
        "expect": "Drafts DIRECTLY even if uncertain — should NOT ask a "
        "clarifying question, since this cluster is a known "
        "customer-can't-help-anyway case.",
        "turns": [
            "my order submitted from crm is stuck and not progressing to completion"
        ],
    },
    {
        "name": "5. Boundary case — exactly 5 words (should NOT trigger Message Analyzer)",
        "expect": "5 words meets the >=5 threshold, so it should go straight "
        "to classification, not a clarifying question.",
        "turns": ["internet connection is very slow"],
    },
    {
        "name": "6. Change category on confirmation",
        "expect": "After a draft, replying with a change request should "
        "either accept a valid category or ask for clarification if the "
        "suggested one doesn't exist.",
        "turns": [
            "I want to cancel my broadband package and get a refund for this month",
            "2 Billing Related",
        ],
    },
    {
        "name": "7. Ticket status inquiry (ticket_status_agent branch)",
        "expect": "Routes to the ticket-viewing branch (tool call to "
        "get_user_tickets), not the ticket-creation branch.",
        "turns": ["What is the status of my open tickets?"],
    },
    {
        "name": "8. Greeting interrupts a mid-flow ticket draft",
        "expect": "Starting a ticket flow then greeting mid-flow should "
        "reset state and greet back, not continue the ticket flow.",
        "turns": [
            "Failed",  # triggers Message Analyzer clarification, mid-flow
            "Good morning",  # standalone greeting during that mid-flow
        ],
    },
]


async def run_conversation(client: httpx.AsyncClient, convo: dict) -> list[tuple[str, str]]:
    thread_id = f"workflow_test_{uuid.uuid4().hex[:10]}"
    results = []
    for turn_msg in convo["turns"]:
        reply = await send_turn(client, thread_id, turn_msg)
        results.append((turn_msg, reply))
    return results


async def main():
    async with httpx.AsyncClient() as client:
        for convo in CONVERSATIONS:
            print(f"\n{'=' * 90}")
            print(f"{convo['name']}")
            print(f"Expected: {convo['expect']}")
            print("-" * 90)
            start = time.monotonic()
            try:
                results = await run_conversation(client, convo)
                for i, (user_msg, reply) in enumerate(results, start=1):
                    print(f"  [Turn {i}] USER: {user_msg!r}")
                    preview = reply.strip().replace("\n", " ")[:300]
                    print(f"  [Turn {i}] AGENT: {preview}")
                elapsed = time.monotonic() - start
                print(f"  (completed in {elapsed:.1f}s)")
            except Exception as exc:
                print(f"  ERROR: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
