"""
System prompts for the helpdesk agent graph, split by pipeline stage (see
each module). Re-exported here so callers can write
`from domain.helpdesk.prompts import X` regardless of which file X lives in.

Some of these prompts are parsed downstream by plain-Python string
matching — see research_prompts.py and category_prompts.py for which
phrases must stay intact.
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
