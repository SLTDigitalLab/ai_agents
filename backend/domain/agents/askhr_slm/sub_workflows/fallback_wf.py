#Greetings: High-confidence small talk ("Hi", "Good morning") generates a prompt warm intro 
#Low-Confidence Fallback: Queries that fail classification routing gracefully receive a structured fallback message or generic assistance prompt.


import logging
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from core.llm_slm import get_slm_chat_model
from domain.agents.askhr_slm.state import AskHRSLMAgentState

log = logging.getLogger(__name__)
llm = get_slm_chat_model()


async def greeting_or_fallback_node(state: AskHRSLMAgentState) -> dict:
    """Handles warm greetings and low-confidence fallback turns."""
    intent = state.get("predicted_intent", "GREETING")

    if intent == "GREETING":
        system_prompt = (
            "Respond with a short, warm greeting. Briefly mention you are the "
            "Ask HR assistant powered by SLTMobitel's internal SLM. Do not cite sources."
        )
        messages = [{"role": "system", "content": system_prompt}] + state["messages"]
        response = await llm.ainvoke(messages)
        return {"messages": [response]}
    
    else:
        # Low confidence or out-of-scope intent fallback
        fallback_msg = (
            "I'm sorry, I couldn't fully understand your request. "
            "You can ask me about your personal leave balance, or search for "
            "SLTMobitel HR company policies (e.g., medical leave, working hours, benefits)."
        )
        return {"messages": [AIMessage(content=fallback_msg)]}


# Workflow Assembly
fallback_builder = StateGraph(AskHRSLMAgentState)
fallback_builder.add_node("handle_fallback", greeting_or_fallback_node)

fallback_builder.add_edge(START, "handle_fallback")
fallback_builder.add_edge("handle_fallback", END)

# Compiled child graph
fallback_subgraph = fallback_builder.compile()