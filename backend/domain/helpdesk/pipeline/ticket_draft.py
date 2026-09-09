"""
Second and final steps of ticket creation (after duplicates.check_duplicates
finds nothing): classify the issue into a category, present the draft for
confirmation, then persist it.

draft_ticket() picks a category via category_classification.
classify_ticket_category_pipeline() and either presents a draft ticket for
the user to confirm, or — if the classifier's self-consistency passes
disagreed and the disagreement isn't confined to the known-unresolvable
cluster — asks one clarifying question first (handled on the next turn by
category_clarification_handler()). confirm_category_handler() processes
the user's keep/change reply, and save_ticket() writes the final ticket to
Postgres.
"""

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


# [NODE] Second step of ticket creation (after check_duplicates finds
# nothing): picks a category via classify_ticket_category_pipeline() and
# either presents a draft ticket for the user to confirm, or — if the
# classifier's self-consistency passes disagreed and the disagreement
# isn't confined to the known-unresolvable cluster — asks one clarifying
# question first (see the Confidence Check stage; handled on the next
# turn by category_clarification_handler()). Ends with ticket_phase =
# "awaiting_category_confirmation" or "awaiting_category_clarification".
async def draft_ticket(state: AgentState) -> dict:
    """Message Analyzer gate, then classify the user's issue via
    classify_ticket_category_pipeline() (Hybrid Retrieval [KB + real-
    ticket-examples merged/reranked] -> Hierarchical LLM Classifier ->
    self-consistency Confidence Check), then either present the ticket
    draft, or ask one clarifying question first — either because the
    message itself was too thin to classify at all, or because
    classification ran but came back low-confidence and worth asking
    about.

    Message Analyzer (added 2026-08-09): runs BEFORE classification, not
    just earlier in the conversation like validate_kb_answer's similar
    vague-query check (kb_search.py — which only fires when the KB search
    ALSO already failed, a narrower, reused mechanism, not a dedicated gate
    here). This one is unconditional: any message under
    _VAGUE_QUERY_MIN_WORDS words that reaches draft_ticket skips
    classify_ticket_category_pipeline() entirely — saving its 6-LLM-call
    cost on input that was never going to classify well anyway — and asks
    for detail via the SAME clarification phase/handler the Confidence
    Check branch below uses (category_clarification_handler), so a ticket
    can only ever be clarified once total, whichever reason triggered it.

    Swapped in 2026-08-09, replacing classify_ticket_category_vector() —
    measured +8.0 points real-traffic accuracy (34.5% -> 42.5% on
    domain/helpdesk/data/representative_eval_200.xlsx). self_consistency_n=3 here
    (not the cheaper n=1 first tried) specifically so confidence is a real
    signal for the Confidence Check branch below — n=1 always reports
    confidence=1.0 (nothing to vote against), which would make that branch
    dead code. n=3 measured 44.5% accuracy (+2.0 over n=1) for 3x more LLM
    calls (6x total vs. classify_ticket_category_vector()'s single call):
    paying for the extra self-consistency passes is only worth it because
    the Confidence Check now actually uses them. See
    helpdesk-category-accuracy-gap project memory for the full comparison.
    classify_ticket_category_vector() and classify_ticket_category_
    examples() remain available, unswapped, for eval comparison and as a
    quick rollback if needed.

    classify_ticket_category() (the older full-category-list prompt flow)
    and list_categories_tool remain unchanged and are still used by
    run_accuracy_eval.py --method prompt for comparison; this node just no
    longer calls them live.
    """
    original_query = state.get("helpdesk_original_query", "") or _latest_user_message(
        state
    )
    messages = state.get("messages", [])
    ticket_id = state.get("helpdesk_draft_ticket_id") or next_ticket_id()
    continuation = _continues_prior_reply(state)
    already_clarified = state.get("helpdesk_category_clarify_count", 0) > 0

    # ── Message Analyzer ─────────────────────────────────────────────────
    # Unconditional gate, runs before classify_ticket_category_pipeline is
    # ever called — see the docstring above for how this differs from
    # validate_kb_answer's similar-looking but narrower, conditional check.
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


# [NODE] Re-entry point when draft_ticket asked a confidence-check
# clarifying question (ticket_phase == "awaiting_category_clarification")
# and the user has just replied. Merges the reply into the original issue
# description and re-classifies EXACTLY ONCE MORE — helpdesk_category_
# clarify_count is already 1 by the time we get here, so draft_ticket's
# own low-confidence branch won't fire again even if the second pass is
# still uncertain; this always ends in a ticket draft, never a second
# clarifying question, keeping the loop capped at one re-ask.
async def category_clarification_handler(state: AgentState) -> dict:
    """Combine the original issue description with the user's clarifying
    answer, re-run classify_ticket_category_pipeline() once, and present
    the ticket draft — same output shape as draft_ticket()'s normal path."""
    prior_query = state.get("helpdesk_original_query", "") or ""
    clarification_reply = _latest_user_message(state)
    combined_query = (
        f"{prior_query} Additional details: {clarification_reply}"
        if prior_query
        else clarification_reply
    )

    messages = state.get("messages", [])
    ticket_id = state.get("helpdesk_draft_ticket_id") or next_ticket_id()
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


# [NODE] Fires when ticket_phase == "awaiting_category_confirmation" — the
# user is replying to draft_ticket's "1. Keep / 2. Change" prompt. Keyword
# detection decides KEEP/CHANGE/UNCLEAR; any user-suggested category on
# CHANGE is re-validated against the DB category list.
async def confirm_category_handler(state: AgentState) -> dict:
    """Process the category confirmation using keyword detection.

    KEEP  → category already validated by draft_ticket, proceed to save.
    CHANGE → validate the user's suggested category against the DB; if not found,
             show the valid main-category names and ask them to try again.
    UNCLEAR → LLM re-prompts (streams naturally).
    """
    user_message = _latest_user_message(state)
    user_lower = re.sub(r"[^\w\s]", "", user_message.lower()).strip()
    words = set(user_lower.split())
    main_category = state.get("helpdesk_draft_main_category", "")
    sub_category = state.get("helpdesk_draft_sub_category", "")

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

        # ── Load all valid categories from DB ────────────────────────────
        try:
            all_categories = list_categories()
        except Exception as exc:
            print(f"[confirm_category_handler] list_categories() error: {exc}")
            all_categories = []

        valid_categories: list[tuple[str, str]] = [
            (c.get("category_name", ""), c.get("subcategory", ""))
            for c in all_categories
            if c.get("category_name")
        ]

        if suggested_main and valid_categories:
            # 1. Exact match on BOTH main category and subcategory, when the
            #    user specified both (e.g. "Network/WiFi Issues"). Must be
            #    checked as a pair before any main-category-only match, or a
            #    main-category hit would grab whichever subcategory sorts
            #    first under that name and silently ignore suggested_sub.
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

            # 2. Exact match on main category name only (no subcategory hint,
            #    or none of that category's subcategories matched it).
            if not matched:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if cn.lower() == suggested_main.lower()),
                    None,
                )
            # 3. Partial match on main category name
            if not matched:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if suggested_main.lower() in cn.lower()
                     or cn.lower() in suggested_main.lower()),
                    None,
                )
            # 4. Match on subcategory alone when user specified one but no
            #    main-category match was found above.
            if not matched and suggested_sub:
                matched = next(
                    ((cn, sc) for cn, sc in valid_categories
                     if sc.lower() == suggested_sub.lower()
                     or suggested_sub.lower() in sc.lower()),
                    None,
                )

            if matched:
                main_category, sub_category = matched
                print(f"[confirm_category_handler] → CHANGE (valid) {main_category!r}/{sub_category!r}")
                return {
                    "helpdesk_ticket_phase": "saving_ticket",
                    "helpdesk_draft_main_category": main_category,
                    "helpdesk_draft_sub_category": sub_category,
                }

            # Category not found — show valid list and ask again
            print(f"[confirm_category_handler] → CHANGE (not found: {suggested_main!r}) — re-asking")
            unique_names = sorted({cn for cn, _ in valid_categories})
            category_list = "\n".join(f"• {cn}" for cn in unique_names)
            response = await llm.ainvoke([
                {
                    "role": "system",
                    "content": category_not_found_system_prompt(
                        suggested_main,
                        category_list,
                        state.get("helpdesk_draft_main_category", ""),
                        state.get("helpdesk_draft_sub_category", ""),
                    ),
                },
                {"role": "user", "content": user_message},
            ])
            return {
                "messages": [AIMessage(content=_message_to_text(response))],
                "helpdesk_ticket_phase": "awaiting_category_confirmation",
            }

        # User said "change" but gave no category — ask what they want
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
            "helpdesk_ticket_phase": "awaiting_category_confirmation",
        }

    # UNCLEAR — ask again via streaming LLM so the user sees the prompt
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
        "helpdesk_ticket_phase": "awaiting_category_confirmation",
    }


# [ROUTER] Category confirmed/changed successfully → save the ticket;
# still unclear or not found → end and wait for the user to try again.
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


# [NODE] Terminal step of ticket creation: writes the ticket to Postgres,
# replies with the confirmation, and clears every helpdesk_* phase field
# back to empty so the next message starts a fully fresh turn.
async def save_ticket(state: AgentState) -> dict:
    """Persist the ticket to the database and return a confirmation message."""
    user_id = state.get("user_id", "anonymous")
    original_query = state.get("helpdesk_original_query", "") or ""
    ticket_id = state.get("helpdesk_draft_ticket_id", "")
    main_category = state.get("helpdesk_draft_main_category", "General")
    sub_category = state.get("helpdesk_draft_sub_category", "General")

    try:
        ticket = create_helpdesk_ticket(
            user_id=user_id,
            message=original_query,
            ticket_id=ticket_id,
            status="open",
            main_category=main_category,
            sub_category=sub_category,
        )
        saved_id = ticket.get("ticket_id", ticket_id)
        msg = (
            f"Your support ticket has been created successfully!\n\n"
            f"**Ticket ID:** {saved_id}\n"
            f"**Status:** Open\n"
            f"**Category:** {main_category} / {sub_category}\n\n"
            "Our support team will review your ticket and get back to you. "
            "You can check your ticket status anytime."
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
