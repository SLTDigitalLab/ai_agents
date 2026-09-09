"""
System prompts for the helpdesk agent graph (backend/domain/helpdesk/pipeline/).

Split by pipeline stage — see each module for its own prompts:
  shared.py             -> _CONTINUATION_NOTE, shared by every stage below
  classifier_prompts.py -> classify_message (STEP 1 — classifier.py)
  greeting_prompts.py   -> greeting_agent (greeting.py)
  research_prompts.py   -> solved-ticket research, KB search, self-or-human,
                            duplicate check (research.py / kb_search.py /
                            self_or_human.py / duplicates.py)
  category_prompts.py   -> ticket drafting + category classification
                            (ticket_draft.py / category_classification.py)
  ticket_status_agent_prompts.py -> "what's my ticket status" (ticket_status_agent.py)

Static prompts are plain string constants. Prompts that need to embed
per-request data (query text, ticket id, category list, etc.) are exposed
as small functions returning the formatted string. This __init__ re-exports
everything so callers can keep writing
`from domain.helpdesk.prompts import X` regardless of which file X lives in.

IMPORTANT — OUTPUT CONTRACTS: several of these prompts are parsed by
plain-Python string/regex matching downstream in domain/helpdesk/pipeline/ —
see the docstring at the top of research_prompts.py and category_prompts.py
for exactly which literal phrases must stay intact.
"""

from domain.helpdesk.prompts.shared import _CONTINUATION_NOTE
from domain.helpdesk.prompts.classifier_prompts import CLASSIFIER_SYSTEM_PROMPT
from domain.helpdesk.prompts.greeting_prompts import GREETING_SYSTEM_PROMPT
from domain.helpdesk.prompts.research_prompts import (
    RESEARCH_SYSTEM_PROMPT,
    SATISFACTION_CLOSING_SYSTEM_PROMPT,
    kb_search_system_prompt,
    SELF_OR_HUMAN_TICKET_SYSTEM_PROMPT,
    SELF_OR_HUMAN_SELF_SYSTEM_PROMPT,
    SELF_OR_HUMAN_MORE_INFO_SYSTEM_PROMPT,
    duplicate_found_system_prompt,
    vague_query_clarification_system_prompt,
)
from domain.helpdesk.prompts.category_prompts import (
    draft_ticket_system_prompt,
    draft_ticket_presentation_system_prompt,
    category_vector_system_prompt,
    category_examples_system_prompt,
    category_hierarchical_main_system_prompt,
    category_hierarchical_sub_system_prompt,
    category_finetuned_system_prompt,
    category_not_found_system_prompt,
    category_no_hint_system_prompt,
    category_unclear_system_prompt,
    category_clarification_system_prompt,
)
from domain.helpdesk.prompts.ticket_status_agent_prompts import ticket_status_agent_system_prompt

__all__ = [
    "_CONTINUATION_NOTE",
    "CLASSIFIER_SYSTEM_PROMPT",
    "GREETING_SYSTEM_PROMPT",
    "RESEARCH_SYSTEM_PROMPT",
    "SATISFACTION_CLOSING_SYSTEM_PROMPT",
    "kb_search_system_prompt",
    "SELF_OR_HUMAN_TICKET_SYSTEM_PROMPT",
    "SELF_OR_HUMAN_SELF_SYSTEM_PROMPT",
    "SELF_OR_HUMAN_MORE_INFO_SYSTEM_PROMPT",
    "duplicate_found_system_prompt",
    "vague_query_clarification_system_prompt",
    "draft_ticket_system_prompt",
    "draft_ticket_presentation_system_prompt",
    "category_vector_system_prompt",
    "category_examples_system_prompt",
    "category_hierarchical_main_system_prompt",
    "category_hierarchical_sub_system_prompt",
    "category_finetuned_system_prompt",
    "category_not_found_system_prompt",
    "category_no_hint_system_prompt",
    "category_unclear_system_prompt",
    "category_clarification_system_prompt",
    "ticket_status_agent_system_prompt",
]
