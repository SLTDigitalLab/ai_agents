"""
Bits shared across every helpdesk prompt module.
"""

# Appended to a prompt (via each function's `continuation` flag) when
# another node already replied earlier in the same turn, so the two
# streamed replies don't read as one run-on sentence.
_CONTINUATION_NOTE = """
CONTINUING AN IN-PROGRESS REPLY
The user was already shown another message from you moments ago, in this
same turn, without sending anything new themselves. Begin your reply with a
blank line (two newline characters), then a short natural transition (for
example "Good news —" or "In the meantime,"), so it visually reads as a
separate message rather than a continuation of the same sentence."""
