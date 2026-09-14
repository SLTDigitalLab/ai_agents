"""
Second and final steps of ticket creation (after duplicates.check_duplicates
finds nothing): classify the issue into a category, present the draft for
confirmation, then persist it.

draft_ticket() picks a category and presents a draft, or asks one
clarifying question first if the classifier wasn't confident
(category_clarification_handler() handles the reply). confirm_category_handler()
processes keep/change — on change, the new category is re-validated against
the DB and the updated draft is shown again for one more confirmation
before anything is saved. Only KEEP leads to save_ticket() writing to
Postgres.
"""

import asyncio
import re
from typing import Literal

from langchain_core.messages import AIMessage

from domain.state import AgentState
from domain.helpdesk.pipeline.helpers import (
    llm,
    _message_to_text,
    _latest_user_message,
    _continues_prior_reply,
    _is_query_too_vague,
    _ai_reply_or_fallback,
)
from domain.helpdesk.pipeline.category_classification import (
    classify_ticket_category_pipeline,
    _CATEGORY_CONFIDENCE_THRESHOLD,
    _AMBIGUOUS_CATEGORY_CLUSTER,
)
from domain.helpdesk.prompts import (
    vague_query_clarification_system_prompt,
    category_clarification_system_prompt,
    draft_ticket_presentation_system_prompt,
    category_not_found_system_prompt,
    category_no_hint_system_prompt,
    category_unclear_system_prompt,
)
from services.helpdesk_tickets import create_helpdesk_ticket, list_categories, next_ticket_id


# Second step of ticket creation: classifies the issue via
# classify_ticket_category_pipeline() and presents a draft, or — if the
# classifier wasn't confident and the disagreement isn't in the known
# ambiguous cluster — asks one clarifying question first (see
# category_clarification_handler for the reply).
async def draft_ticket(state: AgentState) -> dict:
    """Message Analyzer gate, then classify via
    classify_ticket_category_pipeline() and present the draft (or ask one
    clarifying question first, either because the message was too thin to
    classify or classification came back low-confidence)."""
    original_query = state.get("helpdesk_original_query", "") or _latest_user_message(
        state
    )
    messages = state.get("messages", [])
    # Off the event loop — next_ticket_id() is a synchronous, unpooled
    # psycopg call (see check_duplicates() in duplicates.py for why).
    ticket_id = state.get("helpdesk_draft_ticket_id") or await asyncio.to_thread(next_ticket_id)
    continuation = _continues_prior_reply(state)
    already_clarified = state.get("helpdesk_category_clarify_count", 0) > 0

    # Message Analyzer: unconditional gate before classification runs at
    # all, so an under-5-word message skips the (expensive) classifier.
    if _is_query_too_vague(original_query) and not already_clarified:
        print(
            f"[draft_ticket] ticket_id={ticket_id!r} message too thin "
            f"({original_query!r}) → asking for clarification before classifying"
        )
        response = await llm.ainvoke(
            [
                {
                    "role": "system",
                    "content": vague_query_clarification_system_prompt(
                        original_query, continuation=continuation
                    ),
                },
                *messages,
            ]
        )
        response = _ai_reply_or_fallback(
            response,
            "Could you tell me a bit more about the issue — what's happening and when it started?",
        )
        return {
            "messages": [response],
            "helpdesk_draft_ticket_id": ticket_id,
            "helpdesk_ticket_phase": "awaiting_category_clarification",
            "helpdesk_category_clarify_count": 1,
            "helpdesk_original_query": original_query,
        }

    main_category, sub_category, confidence = await classify_ticket_category_pipeline(
        original_query, self_consistency_n=3
    )
    print(
        f"[draft_ticket] ticket_id={ticket_id!r} "
        f"category={main_category!r}/{sub_category!r} confidence={confidence:.2f}"
    )

    low_confidence = confidence < _CATEGORY_CONFIDENCE_THRESHOLD
    in_unresolvable_cluster = main_category in _AMBIGUOUS_CATEGORY_CLUSTER

    if low_confidence and not in_unresolvable_cluster and not already_clarified:
        print(
            f"[draft_ticket] ticket_id={ticket_id!r} low confidence "
            f"({confidence:.2f}) outside ambiguous cluster → asking for clarification"
        )
        response = await llm.ainvoke(
            [
                {
                    "role": "system",
                    "content": category_clarification_system_prompt(
                        original_query, continuation=continuation
                    ),
                },
                *messages,
            ]
        )
        return {
            "messages": [response],
            "helpdesk_draft_ticket_id": ticket_id,
            "helpdesk_ticket_phase": "awaiting_category_clarification",
            "helpdesk_category_clarify_count": 1,
            "helpdesk_original_query": original_query,
        }

    system_prompt = {
        "role": "system",
        "content": draft_ticket_presentation_system_prompt(
            original_query, ticket_id, main_category, sub_category, continuation=continuation
        ),
    }

    response = await llm.ainvoke([system_prompt, *messages])

    return {
        "messages": [response],
        "helpdesk_draft_ticket_id": ticket_id,
        "helpdesk_ticket_phase": "awaiting_category_confirmation",
        "helpdesk_draft_main_category": main_category,
        "helpdesk_draft_sub_category": sub_category,
        "helpdesk_category_clarify_count": 0,
    }


# Re-entry point once the user replies to draft_ticket's clarifying
# question. Combines the reply with the original query and re-classifies
# once — helpdesk_category_clarify_count is already 1 here, so this always
# ends in a draft, never a second clarifying question.
async def category_clarification_handler(state: AgentState) -> dict:
    """Combine the original issue with the user's clarifying answer,
    re-run classification once, and present the ticket draft."""
    prior_query = state.get("helpdesk_original_query", "") or ""
    clarification_reply = _latest_user_message(state)
    combined_query = (
        f"{prior_query} Additional details: {clarification_reply}"
        if prior_query
        else clarification_reply
    )

    messages = state.get("messages", [])
    ticket_id = state.get("helpdesk_draft_ticket_id") or await asyncio.to_thread(next_ticket_id)
    continuation = _continues_prior_reply(state)

    main_category, sub_category, confidence = await classify_ticket_category_pipeline(
        combined_query, self_consistency_n=3
    )
    print(
        f"[category_clarification_handler] ticket_id={ticket_id!r} "
        f"category={main_category!r}/{sub_category!r} confidence={confidence:.2f}"
    )

    system_prompt = {
        "role": "system",
        "content": draft_ticket_presentation_system_prompt(
            combined_query, ticket_id, main_category, sub_category, continuation=continuation
        ),
    }
    response = await llm.ainvoke([system_prompt, *messages])

    return {
        "messages": [response],
        "helpdesk_draft_ticket_id": ticket_id,
        "helpdesk_ticket_phase": "awaiting_category_confirmation",
        "helpdesk_draft_main_category": main_category,
        "helpdesk_draft_sub_category": sub_category,
        "helpdesk_original_query": combined_query,
    }


# Fires on both "awaiting_category_confirmation" (reply to the first
# draft) and "awaiting_final_confirmation" (reply to the re-presented
# draft after a category change) — same handler; current_phase tracks
# which round is active so a still-unresolved reply loops back to it.
#
# KEEP -> save_ticket. CHANGE -> re-validate against list_categories()
# (the DB's all-categories table); a match re-presents the full draft for
# one more confirmation instead of saving immediately; no match re-shows
# the valid category list and asks again.
async def confirm_category_handler(state: AgentState) -> dict:
    """Process the category confirmation using keyword detection.

    KEEP  → user confirmed the category currently on the draft, proceed to save.
    CHANGE → validate the user's suggested category against the DB; if found,
             re-present the full draft for one more confirmation instead of
             saving directly; if not found, show the valid category names.
    UNCLEAR → LLM re-prompts (streams naturally).
    """
    user_message = _latest_user_message(state)
    user_lower = re.sub(r"[^\w\s]", "", user_message.lower()).strip()
    words = set(user_lower.split())
    main_category = state.get("helpdesk_draft_main_category", "")
    sub_category = state.get("helpdesk_draft_sub_category", "")
    current_phase = state.get("helpdesk_ticket_phase", "") or "awaiting_category_confirmation"

    keep_signals = {"1", "keep", "yes", "ok", "okay", "sure", "confirm", "good", "fine", "correct"}
    change_signals = {"2", "change", "different", "modify", "update", "no", "another", "other"}

    if words & keep_signals and not (words & change_signals):
        print("[confirm_category_handler] → KEEP")
        return {
            "helpdesk_ticket_phase": "saving_ticket",
            "helpdesk_draft_main_category": main_category,
            "helpdesk_draft_sub_category": sub_category,
        }

    if words & change_signals:
        # Extract the category the user wants to switch to
        hint = user_lower
        for w in ["change", "2", "different", "modify", "to", "into", "please", "update", "another", "no"]:
            hint = hint.replace(w, "").strip()

        suggested_main = ""
        suggested_sub = ""
        if "/" in hint:
            parts = hint.split("/", 1)
            suggested_main = parts[0].strip().title()
            suggested_sub = parts[1].strip().title()
        elif hint:
            suggested_main = hint.strip().title()

        try:
            # Off the event loop — see check_duplicates() in duplicates.py.
            all_categories = await asyncio.to_thread(list_categories)
        except Exception as exc:
            print(f"[confirm_category_handler] list_categories() error: {exc}")
            all_categories = []

        valid_categories: list[tuple[str, str]] = [
            (c.get("category_name", ""), c.get("subcategory", ""))
            for c in all_categories
            if c.get("category_name")
        ]

        if suggested_main and valid_categories:
            # Match order matters: category+subcategory pair first, or a
            # main-category-only hit could grab the wrong subcategory.
            matched: tuple[str, str] | None = None
            if suggested_sub:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if cn.lower() == suggested_main.lower()
                     and (sc.lower() == suggested_sub.lower()
                          or suggested_sub.lower() in sc.lower()
                          or sc.lower() in suggested_sub.lower())),
                    None,
                )
            if not matched:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if cn.lower() == suggested_main.lower()),
                    None,
                )
            if not matched:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if suggested_main.lower() in cn.lower()
                     or cn.lower() in suggested_main.lower()),
                    None,
                )
            if not matched and suggested_sub:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if sc.lower() == suggested_sub.lower()
                     or suggested_sub.lower() in sc.lower()),
                    None,
                )

            if matched:
                main_category, sub_category = matched
                print(
                    f"[confirm_category_handler] → CHANGE (valid) "
                    f"{main_category!r}/{sub_category!r} — presenting updated draft "
                    "for final confirmation before saving"
                )
                original_query = state.get("helpdesk_original_query", "") or user_message
                ticket_id = state.get("helpdesk_draft_ticket_id", "")
                continuation = _continues_prior_reply(state)
                response = await llm.ainvoke([
                    {
                        "role": "system",
                        "content": draft_ticket_presentation_system_prompt(
                            original_query, ticket_id, main_category, sub_category,
                            continuation=continuation,
                        ),
                    },
                    {"role": "user", "content": user_message},
                ])
                return {
                    "messages": [response],
                    "helpdesk_ticket_phase": "awaiting_final_confirmation",
                    "helpdesk_draft_main_category": main_category,
                    "helpdesk_draft_sub_category": sub_category,
                }

            print(f"[confirm_category_handler] → CHANGE (not found: {suggested_main!r}) — re-asking")
            unique_names = sorted({cn for cn, _ in valid_categories})
            category_list = "\n".join(f"• {cn}" for cn in unique_names)
            response = await llm.ainvoke([
                {
                    "role": "system",
                    "content": category_not_found_system_prompt(
                        suggested_main,
                        category_list,
                        main_category,
                        sub_category,
                    ),
                },
                {"role": "user", "content": user_message},
            ])
            return {
                "messages": [AIMessage(content=_message_to_text(response))],
                "helpdesk_ticket_phase": current_phase,
            }

        # "change" with no category named — ask what they want
        print("[confirm_category_handler] → CHANGE (no hint) — requesting category")
        unique_names = sorted({cn for cn, _ in valid_categories}) if valid_categories else []
        category_list = "\n".join(f"• {cn}" for cn in unique_names)
        response = await llm.ainvoke([
            {
                "role": "system",
                "content": category_no_hint_system_prompt(category_list),
            },
            {"role": "user", "content": user_message},
        ])
        return {
            "messages": [AIMessage(content=_message_to_text(response))],
            "helpdesk_ticket_phase": current_phase,
        }

    print("[confirm_category_handler] → UNCLEAR")
    response = await llm.ainvoke([
        {
            "role": "system",
            "content": category_unclear_system_prompt(main_category, sub_category),
        },
        {"role": "user", "content": user_message},
    ])
    return {
        "messages": [AIMessage(content=_message_to_text(response))],
        "helpdesk_ticket_phase": current_phase,
    }


def route_after_category_confirmation(
    state: AgentState,
) -> Literal["save_ticket", "__end__"]:
    """After confirm_category_handler: saving → save, unclear → END (wait)."""
    phase = state.get("helpdesk_ticket_phase", "")
    if phase == "saving_ticket":
        print("[router] category confirmed → saving ticket")
        return "save_ticket"
    print("[router] category unclear → waiting for user")
    return "__end__"


# Terminal step: writes the ticket to Postgres and clears the phase fields
# so the next message starts a fresh turn.
async def save_ticket(state: AgentState) -> dict:
    """Persist the ticket to the database and return a confirmation message."""
    user_id = state.get("user_id", "anonymous")
    original_query = state.get("helpdesk_original_query", "") or ""
    ticket_id = state.get("helpdesk_draft_ticket_id", "")
    main_category = state.get("helpdesk_draft_main_category", "General")
    sub_category = state.get("helpdesk_draft_sub_category", "General")

    try:
        # Off the event loop — see check_duplicates() in duplicates.py.
        ticket = await asyncio.to_thread(
            create_helpdesk_ticket,
            user_id=user_id,
            message=original_query,
            ticket_id=ticket_id,
            status="open",
            main_category=main_category,
            sub_category=sub_category,
        )
        saved_id = ticket.get("ticket_id", ticket_id)
        msg = (
            "✅ **Your support ticket has been created!**\n\n"
            f"🎫 **Ticket ID:** {saved_id}\n\n"
            "Please keep this ID handy — you can use it anytime to check your "
            "ticket's status or details.\n\n"
            "Our support team will review it shortly and get back to you. "
            "Thank you for your patience!"
        )
        print(f"[save_ticket] ticket saved id={saved_id!r}")
    except Exception as exc:
        msg = (
            f"I'm sorry, there was an error creating your ticket: {exc}. "
            "Please try again later."
        )
        print(f"[save_ticket] ERROR: {exc}")

    return {
        "messages": [AIMessage(content=msg)],
        "helpdesk_ticket_phase": "",
        "helpdesk_research_phase": "",
        "helpdesk_original_query": "",
        "helpdesk_draft_ticket_id": "",
        "helpdesk_draft_main_category": "",
        "helpdesk_draft_sub_category": "",
        "helpdesk_draft_continuation": False,
        "helpdesk_retry_count": 0,
        "helpdesk_category_clarify_count": 0,
    }
