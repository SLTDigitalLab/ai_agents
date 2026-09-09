"""
Bits shared across every helpdesk prompt module.
"""

# Some replies stream live, token-by-token, straight from the model to the
# user (see routers/chat.py's astream_events loop) — by the time a node's
# Python code sees the final response object, those tokens have already been
# sent. So a leading paragraph break can only be inserted by having the model
# generate it itself; Python can't patch it in after the fact. This note is
# appended to a prompt (via each function's `continuation` flag) whenever a
# DIFFERENT node already produced a user-visible reply earlier in the same
# turn, so two back-to-back replies read as separate messages instead of one
# run-on sentence.
_CONTINUATION_NOTE = """
CONTINUING AN IN-PROGRESS REPLY
The user was already shown another message from you moments ago, in this
same turn, without sending anything new themselves. Begin your reply with a
blank line (two newline characters), then a short natural transition (for
example "Good news —" or "In the meantime,"), so it visually reads as a
separate message rather than a continuation of the same sentence."""
