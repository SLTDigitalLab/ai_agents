"""Routing profiles and thresholds for the Ask SLT supervisor agent.

This module keeps the specialist routing metadata separate from the supervisor
implementation so the routing surface can be tuned later without rewriting the
agent logic.
"""

from __future__ import annotations

SPECIALIST_ROUTING_PROFILES: dict[str, dict[str, object]] = {
    "hr": {
        "display_name": "HR",
        "description": (
            "Human resources matters such as leave, attendance, recruitment, "
            "employee benefits, medical claims, payroll policy questions, "
            "letters, internal people policies, and staff support."
        ),
        "keywords": [
            "leave",
            "annual leave",
            "casual leave",
            "sick leave",
            "attendance",
            "employee benefits",
            "medical claim",
            "recruitment",
            "promotion",
            "warning letter",
            "hr policy",
            "staff policy",
        ],
        "examples": [
            "How many annual leave days do I have?",
            "What is the procedure to apply for medical reimbursement?",
            "Where can I find the attendance policy?",
        ],
    },
    "finance": {
        "display_name": "Finance",
        "description": (
            "Finance and accounting matters such as salary, payroll execution, "
            "allowances, reimbursement, invoices, procurement budgets, payments, "
            "purchase orders, expense claims, and financial guidelines."
        ),
        "keywords": [
            "salary",
            "payroll",
            "allowance",
            "invoice",
            "budget",
            "payment",
            "reimbursement",
            "expense claim",
            "purchase order",
            "procurement",
            "finance policy",
        ],
        "examples": [
            "When is the salary credited?",
            "How do I submit an expense claim?",
            "Who approves a procurement invoice?",
        ],
    },
    "admin": {
        "display_name": "Admin",
        "description": (
            "Administration and office support such as transport, facilities, "
            "building access, seating, office assets, parking, security, "
            "maintenance, and general admin services."
        ),
        "keywords": [
            "transport",
            "facility",
            "office access",
            "parking",
            "security",
            "maintenance",
            "asset request",
            "admin support",
            "seating",
            "building pass",
        ],
        "examples": [
            "How do I request office transport?",
            "Who handles parking access?",
            "How can I report a facility issue?",
        ],
    },
}

GENERAL_HELP_PATTERNS: tuple[str, ...] = (
    r"^\s*(hi|hello|hey|good morning|good afternoon|good evening)\b",
    r"\b(thanks|thank you|bye|goodbye)\b",
    r"\bwhat can you do\b",
    r"\bhow can you help\b",
    r"\bwho are you\b",
    r"\bwhat is ask slt\b",
    r"\bhow does this work\b",
    r"\bhow do i use (this|the platform|ask slt)\b",
    r"\bwhich agent should i use\b",
    r"\bwho handles\b",
    r"\bwhere should i ask\b",
    r"\bavailable agents\b",
    r"\bhelp me choose\b",
    r"\broute me\b",
)

FOLLOW_UP_PATTERNS: tuple[str, ...] = (
    r"^\s*what about( that| this)?\s*\??$",
    r"^\s*how about( that| this)?\s*\??$",
    r"^\s*can i apply\s*\??$",
    r"^\s*can i do that\s*\??$",
    r"^\s*what next\s*\??$",
    r"^\s*how do i apply\s*\??$",
    r"^\s*and that\s*\??$",
    r"^\s*what else\s*\??$",
    r"^\s*how much\s*\??$",
)

# Initial tuning defaults for cosine similarity routing.
# These should be refined later using real routing logs.
STRONG_ROUTE_THRESHOLD = 0.42
LOW_CONFIDENCE_THRESHOLD = 0.30
OUT_OF_SCOPE_THRESHOLD = 0.22
MIN_ROUTE_MARGIN = 0.04
FOLLOW_UP_STICKINESS_BOOST = 0.06
SHORT_FOLLOW_UP_MAX_WORDS = 6