"""
Live test: for real short ticket descriptions (<5 words, the
_VAGUE_QUERY_MIN_WORDS threshold in pipeline/helpers.py) drawn from
domain/helpdesk/data/Incidents_Feb.xlsx, check what the deployed system
actually does — does it stop and ask a clarifying question instead of
guessing a category, and does that question give the customer any
steer toward WHICH KIND of issue/category it needs (vs. a fully generic
"tell me more")?

This is a live end-to-end run against the real /api/v1/chat endpoint
(same pattern as test_bugfix_verify.py / test_full_workflow.py) — each
sample message is sent as the FIRST turn of a fresh thread, so it always
hits the Message Analyzer gate in draft_ticket()/kb_search_agent's
vague-query pre-check before any classification runs.

Usage (from /app inside the backend container, backend must be running):
    python domain/helpdesk/scripts/test_short_message_clarification.py
"""

import asyncio
import re
import uuid

import httpx
import pandas as pd

BASE_URL = "http://localhost:8000/api/v1/chat"
AGENT_ID = "helpdesk_dev"
USER_ID = "short_msg_test_user"

VAGUE_QUERY_MIN_WORDS = 5  # mirrors pipeline/helpers.py _VAGUE_QUERY_MIN_WORDS

# Keywords that would indicate the clarifying question is actually
# steering the customer toward a category/issue-type distinction,
# rather than asking a fully generic "tell me more" question.
CATEGORY_HINT_RE = re.compile(
    r"\b(billing|bill|payment|order|new (service|connection|order)|"
    r"existing (service|connection|order)|technical|connection|"
    r"internet|broadband|installation|activation|cancel|refund|"
    r"error|fail(ed|ing)?|status|category|type of (issue|problem)|"
    r"what.{0,15}(kind|type) of)\b",
    re.IGNORECASE,
)


def sample_short_messages(n_per_category: int = 2) -> list[dict]:
    df = pd.read_excel("domain/helpdesk/data/Incidents_Feb.xlsx")
    df["desc"] = df["DESCRIPTION"].astype(str).str.strip()
    df["wc"] = df["desc"].apply(lambda t: len(t.split()) if t and t.lower() != "nan" else 0)
    short = df[(df["wc"] > 0) & (df["wc"] < VAGUE_QUERY_MIN_WORDS)].drop_duplicates("desc")

    samples = []
    for main_cat, group in short.groupby("INCIDENT_TYPE_CD"):
        picked = group.sample(n=min(n_per_category, len(group)), random_state=42)
        for _, row in picked.iterrows():
            samples.append(
                {
                    "message": row["desc"],
                    "true_category": main_cat,
                    "true_sub_category": row.get("INC_SUB_TYPE_CD", ""),
                    "word_count": row["wc"],
                }
            )
    return samples


async def send_turn(client: httpx.AsyncClient, thread_id: str, message: str) -> str:
    resp = await client.post(
        BASE_URL,
        json={"message": message, "agent_id": AGENT_ID, "user_id": USER_ID, "thread_id": thread_id},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.text


async def run_one(client: httpx.AsyncClient, sample: dict) -> dict:
    thread_id = f"shortmsg_{uuid.uuid4().hex[:10]}"
    reply = await send_turn(client, thread_id, sample["message"])

    drafted_ticket = "**Category:**" in reply or "Ticket ID:" in reply
    asked_clarification = not drafted_ticket and len(reply.strip()) > 0
    gave_category_hint = bool(CATEGORY_HINT_RE.search(reply))

    return {
        **sample,
        "reply": reply.strip(),
        "drafted_ticket_immediately": drafted_ticket,
        "asked_clarification": asked_clarification,
        "clarification_gave_category_hint": gave_category_hint if asked_clarification else None,
    }


async def main():
    samples = sample_short_messages(n_per_category=2)
    print(f"Sampled {len(samples)} short (<{VAGUE_QUERY_MIN_WORDS}-word) real ticket descriptions "
          f"across {len(set(s['true_category'] for s in samples))} categories from Incidents_Feb.xlsx\n")

    results = []
    async with httpx.AsyncClient() as client:
        for i, sample in enumerate(samples, 1):
            print(f"[{i}/{len(samples)}] \"{sample['message']}\" (true: {sample['true_category']})")
            try:
                result = await run_one(client, sample)
            except Exception as exc:
                print(f"    ERROR: {exc}")
                result = {**sample, "reply": f"ERROR: {exc}", "drafted_ticket_immediately": None,
                          "asked_clarification": None, "clarification_gave_category_hint": None}
            results.append(result)
            gate_status = (
                "asked clarification" if result["asked_clarification"]
                else "drafted ticket immediately" if result["drafted_ticket_immediately"]
                else "ERROR"
            )
            hint_status = (
                "" if result["clarification_gave_category_hint"] is None
                else " [category-relevant hint: YES]" if result["clarification_gave_category_hint"]
                else " [category-relevant hint: NO - generic]"
            )
            print(f"    -> {gate_status}{hint_status}")
            print(f"    reply: {result['reply'][:220]}")
            print()

    df_out = pd.DataFrame(results)
    n = len(df_out)
    n_clarified = df_out["asked_clarification"].sum()
    n_drafted = df_out["drafted_ticket_immediately"].sum()
    n_hinted = df_out.loc[df_out["asked_clarification"] == True, "clarification_gave_category_hint"].sum()

    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total short messages tested: {n}")
    print(f"Gate correctly triggered clarification (not immediate draft): {n_clarified}/{n} ({n_clarified/n*100:.0f}%)")
    print(f"Drafted a ticket immediately despite short message: {n_drafted}/{n} ({n_drafted/n*100:.0f}%)")
    if n_clarified:
        print(f"Of those clarifications, how many gave a category/issue-type-relevant hint "
              f"(mentioned billing/order/technical/new-vs-existing/etc., not just \"tell me more\"): "
              f"{n_hinted}/{n_clarified} ({n_hinted/n_clarified*100:.0f}%)")

    out_path = "domain/helpdesk/data/short_message_clarification_test.xlsx"
    df_out.to_excel(out_path, index=False)
    print(f"\nWrote per-message detail to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
