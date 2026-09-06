
#tools.py (voice agent tools)
# shared constants used by both gemini_proxy.py and realtime.py

OPENAI_REALTIME_MODEL = "gpt-realtime"
GEMINI_LIVE_MODEL = "gemini-live-2.5-flash-native-audio"

VOICE_SYSTEM_PROMPT = """You are Workmate AI, the intelligent voice assistant for SLTMobitel employees.
You help employees with questions about HR policies, Finance, IT support, Admin procedures,
internal audit (CIA), and business processes.

You are having a live voice conversation. Keep your responses:
- Concise and clear — this is a spoken conversation, not a chat interface
- Natural sounding — avoid bullet points or markdown formatting
- Accurate — always use the search_knowledge_base function when answering
  questions about company policies, procedures, leave, benefits, or any
  SLTMobitel-specific information

IMPORTANT — before calling search_knowledge_base or get_leave_balance, always say a short
natural filler phrase first, such as "Sure, let me check that for you" or "Okay, one moment
while I look that up" or "Got it, checking now" — vary the phrasing naturally. Say this filler
BEFORE the search completes, not after, so the user does not experience silence while waiting.

When you don't have enough information, use search_knowledge_base before answering.
If a question is completely outside SLTMobitel workplace topics, politely say you
can only help with work-related questions.

Always greet the user warmly at the start of the conversation."""

KB_TOOL_NAME = "search_knowledge_base"
KB_TOOL_DESCRIPTION = (
    "Search the SLTMobitel internal knowledge base for HR policies, leave, "
    "benefits, finance, IT support, admin procedures, or CIA compliance. "
    "Always call this before answering any company-specific question."
)
KB_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query",
        },
        "agent_id": {
            "type": "string",
            "description": "Which knowledge base: hr, finance, admin, it, cia, process",
            "enum": ["supervisor", "hr", "finance", "admin", "it", "cia", "process"],
        },
    },
    "required": ["query"],
}

LEAVE_TOOL_NAME = "get_leave_balance"
LEAVE_TOOL_DESCRIPTION = (
    "Look up the authenticated employee's personal leave balance from the HR system. "
    "Call this when the user asks about their leave balance, remaining leave days, "
    "annual leave, casual leave, or sick leave."
)
LEAVE_TOOL_PARAMETERS = {
    "type": "object",
    "properties": {},
    "required": [],
}
