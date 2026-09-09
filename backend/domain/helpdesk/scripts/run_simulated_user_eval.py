"""
"Acting as a real user" category-prediction eval, using
domain/helpdesk/data/Incidents_Feb.xlsx as ground truth.

Every prior eval (run_accuracy_eval.py, run_pipeline_eval.py,
test_short_message_clarification.py, etc.) fed the raw DESCRIPTION field
straight into the classifier/chat endpoint — but that field is an internal
agent's shorthand note ("1-66059509903 plz complete this order",
"SIEBEL AC FAILED"), not what a real customer types into a chat window.
This script closes that gap: an LLM plays the CUSTOMER, rewriting each
raw internal note into a natural first-person chat message (no internal
jargon/system names a customer wouldn't know), THEN that simulated
message is sent through the real live chat endpoint exactly like a real
conversation — including answering one clarifying follow-up in character
if the system asks one (mirrors the live system's own single-reask cap).
The final drafted ticket's category/sub-category is parsed out and
compared against the ticket's real historical category.

Usage (from /app inside the backend container, backend must be running):
    python domain/helpdesk/scripts/run_simulated_user_eval.py --n-per-category 3
"""

import argparse
import asyncio
import re
import sys
import uuid

import httpx
import pandas as pd

sys.path.insert(0, "/app")

from domain.helpdesk.pipeline.helpers import llm  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

BASE_URL = "http://localhost:8000/api/v1/chat"
AGENT_ID = "helpdesk_dev"
USER_ID = "sim_user_eval"

CATEGORY_RE = re.compile(r"\*\*Category:\*\*\s*(.+)")
SUBCATEGORY_RE = re.compile(r"\*\*Sub-category:\*\*\s*(.+)")

PERSONA_SYSTEM_PROMPT = """You are simulating a REAL SLT Mobitel customer about to message a helpdesk \
chatbot for the first time about a problem.

Below is the raw internal note a support agent wrote for a real historical case. It is terse, full \
of internal jargon, abbreviations, system names, and order/account numbers - written BY an agent \
FOR internal use, not something a customer ever wrote or would read.

Your job: rewrite it as the CUSTOMER's own first chat message, in natural, plain, everyday English \
(a sentence or two, like a real chat message - not a formal report).

Rules:
- NEVER mention internal system/team names (CRM, OSS, SIEBEL, BSS, SOA, Clarity, TT, SR, OLI, DM, \
MDM, WO, ACCT, etc.) or internal jargon a customer would not know or say - describe the SYMPTOM the \
customer would actually observe instead (e.g. "my order isn't going through", "I was charged for \
something I already cancelled", "I can't verify my order online").
- Keep any account/order/reference number present in the note - a real customer often does quote \
these.
- Phrase it as REPORTING A PROBLEM that needs fixing, NOT as asking someone to "check the status" of \
an existing support ticket/reference number - e.g. prefer "my order hasn't gone through, please help \
me get it completed" over "can you check what's happening with reference X". The customer is reaching \
the help desk for the first time about this, not following up on a ticket they already filed.
- Do not invent facts that aren't implied by the note.
- Do not state or hint at the category/team name - the whole point is that the customer wouldn't \
know it.
- Output ONLY the customer's chat message text. No preamble, no quotes, no explanation."""

PERSONA_FOLLOWUP_SYSTEM_PROMPT = """You are continuing to role-play as the SAME customer from before, \
in the same chat conversation.

Original internal note this case is based on (ground truth the customer implicitly "knows", but \
would never phrase this way): "{raw_note}"

Your first message was: "{first_message}"

The helpdesk agent just replied:
"{agent_reply}"

Write the customer's next chat message, briefly and naturally answering the agent's question using \
only information a real customer would know (you may infer plausible customer-facing detail from the \
original note above, e.g. whether it's a new order or an existing service, roughly when it started, \
what error/symptom they saw - but do not use any internal system/team names). If the agent's question \
genuinely can't be answered from the note, give a plausible, natural customer answer rather than \
saying you don't know. Output ONLY the customer's reply text, nothing else."""


async def _llm_text(system_prompt: str, temperature: float = 0.8) -> str:
    model = llm.bind(temperature=temperature)
    resp = await model.ainvoke([SystemMessage(content=system_prompt)])
    content = resp.content
    if isinstance(content, list):
        content = " ".join(
            (b if isinstance(b, str) else b.get("text", "")) for b in content
        )
    return str(content).strip().strip('"')


def sample_tickets(n_per_category: int, seed: int = 42) -> list[dict]:
    df = pd.read_excel("domain/helpdesk/data/Incidents_Feb.xlsx")
    df["desc"] = df["DESCRIPTION"].astype(str).str.strip()
    df = df[(df["desc"] != "") & (df["desc"].str.lower() != "nan")].drop_duplicates("desc")

    samples = []
    for main_cat, group in df.groupby("INCIDENT_TYPE_CD"):
        picked = group.sample(n=min(n_per_category, len(group)), random_state=seed)
        for _, row in picked.iterrows():
            samples.append(
                {
                    "raw_note": row["desc"],
                    "true_category": main_cat,
                    "true_sub_category": row.get("INC_SUB_TYPE_CD", ""),
                }
            )
    return samples


async def send_turn(client: httpx.AsyncClient, thread_id: str, message: str) -> str:
    resp = await client.post(
        BASE_URL,
        json={"message": message, "agent_id": AGENT_ID, "user_id": USER_ID, "thread_id": thread_id},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.text


def _parse_category(reply: str) -> tuple[str, str]:
    cat_m = CATEGORY_RE.search(reply)
    sub_m = SUBCATEGORY_RE.search(reply)
    return (cat_m.group(1).strip() if cat_m else "", sub_m.group(1).strip() if sub_m else "")


MAX_TURNS = 6

# KB self-service asks some form of "did this solve/resolve your issue" -
# either the explicit "1. Yes / 2. No, create a ticket / 3. more info" menu,
# or a free-form "Did this resolve your issue?" ending. Since this eval's
# goal is testing category prediction, the persona always pushes toward
# ticket creation rather than accepting KB self-resolution, so every case
# actually reaches a category decision. A FIXED (non-LLM-generated) reply is
# used here rather than letting the persona-followup LLM phrase it, because
# an LLM-composed "Yes, please still do X" reply leads with "yes" and gets
# keyword-matched as satisfied/resolved (self_or_human_handler's word-set
# match), even though the sentence is actually asking for more action.
_SATISFACTION_CHECK_RE = re.compile(r"\b(resolve|resolves|solved?|solves)\b.{0,40}\?", re.IGNORECASE)


def _fixed_wants_ticket_reply(persona_message: str) -> str:
    """Unambiguous "no, make a ticket" reply that still carries the real
    issue text. A short generic reply like "please create a support ticket
    for me" is >=5 words, so it passes _is_query_too_vague()'s guard and
    (per self_or_human_handler's wants_ticket branch) OVERWRITES the
    tracked original query with itself — wiping out the real complaint
    right before classification runs. Repeating the issue here keeps
    whatever ends up as the tracked query still diagnostic."""
    return f"No, that does not resolve it — {persona_message} Please create a support ticket for me."

# Phrases seen when the router misreads an issue-report as a ticket-status
# lookup (e.g. a message that quotes a reference number) and dead-ends
# instead of drafting. Recovery: restate the issue, escalating to an
# explicit "create a ticket" framing if a plain restatement dead-ends twice
# in a row, since a real customer would eventually say so explicitly.
_STATUS_LOOKUP_DEADEND_MARKERS = [
    "outside what I can do here",
    "No tickets found",
    "no tickets were found",
    "describe the issue as a new message",
]


def _is_status_lookup_deadend(reply: str) -> bool:
    return any(marker.lower() in reply.lower() for marker in _STATUS_LOOKUP_DEADEND_MARKERS)


def _is_satisfaction_check(reply: str) -> bool:
    return bool(_SATISFACTION_CHECK_RE.search(reply)) or "please create a support ticket" in reply.lower()


async def run_one(sem: asyncio.Semaphore, client: httpx.AsyncClient, sample: dict) -> dict:
    async with sem:
        raw_note = sample["raw_note"]
        persona_message = await _llm_text(
            PERSONA_SYSTEM_PROMPT + f'\n\nInternal note: "{raw_note}"'
        )

        thread_id = f"simuser_{uuid.uuid4().hex[:10]}"
        turns = []
        next_message = persona_message
        predicted_category, predicted_sub_category = "", ""
        outcome = "max_turns_reached"
        consecutive_deadends = 0

        for turn_num in range(1, MAX_TURNS + 1):
            reply = await send_turn(client, thread_id, next_message)
            turns.append({"role": "customer", "text": next_message})
            turns.append({"role": "agent", "text": reply})

            predicted_category, predicted_sub_category = _parse_category(reply)
            if predicted_category:
                outcome = "ticket_drafted"
                break

            if _is_satisfaction_check(reply):
                next_message = _fixed_wants_ticket_reply(persona_message)
                consecutive_deadends = 0
                continue

            if _is_status_lookup_deadend(reply) and turn_num < MAX_TURNS:
                consecutive_deadends += 1
                if consecutive_deadends == 1:
                    # First dead-end: restate the issue plainly, as a real
                    # customer would after being told to "describe the issue
                    # as a new message".
                    next_message = persona_message
                else:
                    # Still dead-ending on a plain restatement - escalate to
                    # an explicit ticket request, which a real customer would
                    # eventually resort to.
                    next_message = f"I'd like to create a support ticket for this problem: {persona_message}"
                continue

            consecutive_deadends = 0
            # Otherwise it's a genuine clarifying question - answer in character.
            next_message = await _llm_text(
                PERSONA_FOLLOWUP_SYSTEM_PROMPT.format(
                    raw_note=raw_note,
                    first_message=persona_message,
                    agent_reply=reply,
                )
            )

        return {
            **sample,
            "persona_message": persona_message,
            "predicted_category": predicted_category,
            "predicted_sub_category": predicted_sub_category,
            "n_turns": len(turns) // 2,
            "outcome": outcome,
            "transcript": "\n".join(f"[{t['role']}] {t['text']}" for t in turns),
            "main_correct": predicted_category.strip() == str(sample["true_category"]).strip(),
            "sub_correct": predicted_sub_category.strip() == str(sample["true_sub_category"]).strip(),
        }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-per-category", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", default="domain/helpdesk/data/simulated_user_eval.xlsx")
    args = parser.parse_args()

    samples = sample_tickets(args.n_per_category)
    print(f"Sampled {len(samples)} tickets across {len(set(s['true_category'] for s in samples))} "
          f"categories from Incidents_Feb.xlsx\n")

    sem = asyncio.Semaphore(args.concurrency)
    results = []
    async with httpx.AsyncClient() as client:
        tasks = [run_one(sem, client, s) for s in samples]
        done = 0
        for coro in asyncio.as_completed(tasks):
            try:
                r = await coro
            except Exception as exc:
                print(f"  ERROR: {exc}")
                continue
            results.append(r)
            done += 1
            status = "OK  " if r["main_correct"] else "MISS"
            print(f"[{done}/{len(samples)}] {status} true={r['true_category']!r} "
                  f"pred={r['predicted_category']!r} turns={r['n_turns']} outcome={r['outcome']}")
            print(f"    customer said: {r['persona_message'][:160]}")

    df_out = pd.DataFrame(results)
    n = len(df_out)
    reached = df_out[df_out["outcome"] == "ticket_drafted"]
    n_reached = len(reached)
    main_acc = reached["main_correct"].mean() * 100 if n_reached else float("nan")
    both_acc = (reached["main_correct"] & reached["sub_correct"]).mean() * 100 if n_reached else float("nan")
    sub_given_main = (
        reached.loc[reached["main_correct"], "sub_correct"].mean() * 100
        if reached["main_correct"].sum() else float("nan")
    )
    multi_turn_rate = (df_out["n_turns"] > 1).mean() * 100

    print("\n" + "=" * 70)
    print("SUMMARY - Simulated real-user category prediction (Incidents_Feb.xlsx)")
    print("=" * 70)
    print(f"Tickets tested: {n}")
    print(f"Reached a ticket draft (category prediction made): {n_reached}/{n} ({n_reached/n*100:.0f}%)")
    print(f"Outcome breakdown: {df_out['outcome'].value_counts().to_dict()}")
    print(f"Needed 2+ turns before reaching a final outcome: {multi_turn_rate:.1f}%")
    print(f"\nOf tickets that reached a draft:")
    print(f"  Main-category accuracy: {main_acc:.1f}%")
    print(f"  Sub-category accuracy (given main correct): {sub_given_main:.1f}%")
    print(f"  Strict (both main AND sub correct): {both_acc:.1f}%")

    with pd.ExcelWriter(args.output) as writer:
        df_out.to_excel(writer, sheet_name="results", index=False)
    print(f"\nWrote per-ticket detail (incl. full transcripts) to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
