"""Targeted re-test of the two bugs found + fixed on 2026-08-11:
1. Garbled/interleaved text from internal self-consistency LLM calls
   leaking into the user-facing stream.
2. Missing continuation note causing run-on clarification replies.

Usage (from /app inside the backend container, backend must be running):
    python domain/helpdesk/scripts/test_bugfix_verify.py
"""

import asyncio
import uuid

import httpx

BASE_URL = "http://localhost:8000/api/v1/chat"
AGENT_ID = "helpdesk_dev"
USER_ID = "bugfix_verify_user"


async def send_turn(client: httpx.AsyncClient, thread_id: str, message: str) -> str:
    resp = await client.post(
        BASE_URL,
        json={"message": message, "agent_id": AGENT_ID, "user_id": USER_ID, "thread_id": thread_id},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


async def main():
    async with httpx.AsyncClient() as client:
        # Repro case for BOTH bugs: 'Failed' -> clarification (run-on check),
        # then a real message that reaches self_consistency_n=3 classification
        # (garbling check).
        thread_id = f"bugfix_{uuid.uuid4().hex[:10]}"
        print("=== Turn 1: 'Failed' ===")
        reply1 = await send_turn(client, thread_id, "Failed")
        print(reply1)
        print("\n--- run-on check: no glued words like 'directly.Could' ---")
        print("PASS" if "directly.Could" not in reply1 and ".Could" not in reply1 else "FAIL — still glued")

        print("\n=== Turn 2: real detail (triggers 6-call classification) ===")
        reply2 = await send_turn(
            client,
            thread_id,
            "My fiber router lost internet connection this morning and restarting it didn't help",
        )
        print(reply2)
        print("\n--- garbling check: no repeated/interleaved '**Category:**' fragments ---")
        garbled_markers = ["CategoryCategory", "****", ":**:**"]
        found = [m for m in garbled_markers if m in reply2]
        print("PASS" if not found else f"FAIL — found garbled markers: {found}")

        # Second repro: category-change reply that previously triggered the
        # 3-way garbled classification text via self_or_human_handler.
        thread_id2 = f"bugfix_{uuid.uuid4().hex[:10]}"
        print("\n=== Separate thread: substantive issue -> KB found -> '2' (want ticket) ===")
        r1 = await send_turn(
            client, thread_id2, "I want to cancel my broadband package and get a refund for this month"
        )
        print(r1[:200])
        r2 = await send_turn(client, thread_id2, "2")
        print(r2)
        found2 = [m for m in garbled_markers if m in r2]
        print("PASS" if not found2 else f"FAIL — found garbled markers: {found2}")


if __name__ == "__main__":
    asyncio.run(main())
