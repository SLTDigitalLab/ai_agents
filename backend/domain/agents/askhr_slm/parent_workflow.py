#Primary Intent Router -> Sentence transformer
#Fallback Intent Router -> Ollama SLM

#Define Parent Node
#-create "wrapper nodes" that call each sub-workflow
"""
classify_intent_node: Reads the user's message, calls the classifier, and saves predicted_intent and confidence_score into the state.
call_leave_wf_node: Calls leave_balance_subgraph.ainvoke(state) and returns its final message.
call_kb_wf_node: Calls kb_subgraph.ainvoke(state) and returns its final message.
call_fallback_wf_node: Calls fallback_subgraph.ainvoke(state) and returns its final message.

""" 
import logging
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import StateGraph, START, END



# Import state and sub-workflows
from domain.agents.askhr_slm.state import AskHRSLMAgentState
from domain.agents.askhr_slm.sub_workflows.leave_balance_wf import leave_balance_subgraph
from domain.agents.askhr_slm.sub_workflows.kb_wf import kb_subgraph
from domain.agents.askhr_slm.sub_workflows.fallback_wf import fallback_subgraph
from domain.tools.intent_classifier import classify_intent_hybrid
from langgraph.errors import NodeInterrupt

log = logging.getLogger(__name__)

#-----------------------------------------------------------
#2. CONFIGURATION & ROUTER LOGIC
#Tune the threshold for intent classification confidence
#---------------------------------------------------------

# Tunable Confidence Boundaries
HIGH_CONFIDENCE_THRESHOLD = 0.70  # Direct execution cut-off
LOW_CONFIDENCE_THRESHOLD = 0.45   # Fallback cut-off

def route_by_intent(state: AskHRSLMAgentState) -> str:
    #Determines execution path based on intent confidence score or HITL selection.
    score = state.get("confidence_score")
    if score is None:
        score = 0.0
    intent = state.get("predicted_intent", "")

    # 1. If state explicitly set by HITL choice:
    if state.get("is_leave_balance") is True:
        return "call_leave_wf"
    elif state.get("is_leave_balance") is False:
        return "call_kb_wf"

    # 2. Low Confidence (< 0.50) -> Fallback
    if score < LOW_CONFIDENCE_THRESHOLD:
        return "call_fallback_wf"
    
    # 3. High confidence  (>= 0.70) -> Route directly
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        if intent == "LEAVE_BALANCE":
            return "call_leave_wf"
        elif intent == "KB_SEARCH":
            return "call_kb_wf"
        elif intent == "GREETING":
            return "call_fallback_wf"

    # 4. Low Confidence Path (< 0.45 and no valid intent)
    if score < LOW_CONFIDENCE_THRESHOLD:
        return "call_fallback_wf"

    # 5. Medium confidence/ Ambiguous (0.45 <= score < 0.70) -> HITL Clarification
    return "hitl_clarification"


# ----------------------3. PARENT NODE HYBRID APPROACH NODE 1 -----------------------
async def classify_intent_node(state: AskHRSLMAgentState) -> dict:
    #Reads user query, runs hybrid vector + Ollama SLM classifier, updates state.
    messages = state.get("messages", [])
    user_query = messages[-1].content if messages else ""

    # Call the Hybrid Classifier
    predicted_intent, confidence_score = await classify_intent_hybrid(user_query)

    log.info(f"[Parent Node] Final Classification: {predicted_intent} (Score: {confidence_score})")

    return {
        "predicted_intent": predicted_intent,
        "confidence_score": confidence_score
    }



#----------------------------------------------------------------
async def hitl_clarification_node(state: AskHRSLMAgentState) -> dict:
    """Handles Human-in-the-Loop clarification when intent confidence is ambiguous.
    
    1. If no choice has been recorded yet, emits a prompt and interrupts graph execution.
    2. Once resumed with a human answer, parses the response and updates `is_leave_balance`.
    """
    messages = state.get("messages", [])
    user_reply = messages[-1].content.strip().lower() if messages else ""
    
    # CASE 1: User has already responded to the clarification prompt
    if state.get("clarification_pending"):
        log.info(f"[HITL Node] User responded to clarification prompt: {user_reply!r}")
        
        # Analyze user choice (e.g., "personal", "1", "yes", or "policy", "2", "no")
        if any(keyword in user_reply for keyword in ["personal", "my balance", "1", "yes"]):
            log.info("[HITL Node] User selected: Personal Leave Balance")
            return {
                "predicted_intent": "LEAVE_BALANCE",
                "confidence_score": 1.0,
                "is_leave_balance": True,
                "clarification_pending": False
            }
        elif any(keyword in user_reply for keyword in ["policy", "policies", "general", "2", "no"]):
            log.info("[HITL Node] User selected: General HR Policies")
            return {
                "predicted_intent": "KB_SEARCH",
                "confidence_score": 1.0,
                "is_leave_balance": False,
                "clarification_pending": False
            }
        else:
            # Unclear answer provided during HITL step -> default to Fallback
            log.warning("[HITL Node] Unclear response to clarification options.")
            return {
                "predicted_intent": "UNKNOWN",
                "confidence_score": 0.0,
                "is_leave_balance": None, # Reset explicitly
                "clarification_pending": False
            }

    # CASE 2: First time hitting node -> Ask question and interrupt for human input
    log.info("[HITL Node] Emitting clarification question and triggering NodeInterrupt...")
    
    clarification_prompt = (
        "I want to make sure I give you the right answer:\n\n"
        "1. Do you want to check your **personal leave balance** from the ERP?\n"
        "2. Or do you want to search general **company leave policies**?\n\n"
        "Please reply with **1** (Personal) or **2** (Policies)."
    )
    
    # State updates to store before pausing graph execution
    state_updates = {
        "messages": messages + [AIMessage(content=clarification_prompt)],
        "clarification_pending": True
    }
    
    # Interrupt execution and yield control back to the API / caller layer
    raise NodeInterrupt(clarification_prompt)

# NODE 2: Leave Balance Sub-Workflow Wrapper

async def call_leave_wf_node(state: AskHRSLMAgentState) -> dict:
    """Invokes the leave balance child graph asynchronously 
    and passes back its output messages.
    """
    log.info("[Parent Workflow] Routing to Leave Balance Sub-Workflow")
    
    #LangGraph passes the current parent state down to the child graph
    subgraph_response = await leave_balance_subgraph.ainvoke(state)
    
    # Extract updated messages from child graph state
    return {"messages": subgraph_response.get("messages", [])}


# NODE 3: Knowledge Base Sub-Workflow Wrapper

async def call_kb_wf_node(state: AskHRSLMAgentState) -> dict:
    """Invokes the KB RAG child graph asynchronously 
    and passes back its output messages.
    """
    log.info("[Parent Workflow] Routing to KB Sub-Workflow")
    
    subgraph_response = await kb_subgraph.ainvoke(state)
    return {"messages": subgraph_response.get("messages", [])}

# NODE 4: Fallback Sub-Workflow Wrapper
async def call_fallback_wf_node(state: AskHRSLMAgentState) -> dict:
    """Invokes the Fallback/Greeting child graph asynchronously 
    and passes back its output messages.
    """
    log.info("[Parent Workflow] Routing to Fallback Sub-Workflow")
    
    subgraph_response = await fallback_subgraph.ainvoke(state)
    return {"messages": subgraph_response.get("messages", [])}


# 4. GRAPH ASSEMBLY & COMPILATION (Step 4)
# -------------------------------------------------------------------

parent_builder = StateGraph(AskHRSLMAgentState)

# A. Register Nodes
parent_builder.add_node("classify_intent", classify_intent_node)
parent_builder.add_node("hitl_clarification", hitl_clarification_node)
parent_builder.add_node("call_leave_wf", call_leave_wf_node)
parent_builder.add_node("call_kb_wf", call_kb_wf_node)
parent_builder.add_node("call_fallback_wf", call_fallback_wf_node)

# B. Set Entry Point
parent_builder.add_edge(START, "classify_intent")

# C. Mapping Table & Conditional Edges (EXACTLY HERE)
routing_map = {
    "call_leave_wf": "call_leave_wf",
    "call_kb_wf": "call_kb_wf",
    "call_fallback_wf": "call_fallback_wf",
    "hitl_clarification": "hitl_clarification",
}

# Add conditional routing after intent classification
parent_builder.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    routing_map
)

# Add conditional routing after HITL clarification resolves
parent_builder.add_conditional_edges(
    "hitl_clarification",
    route_by_intent,
    routing_map
)

# D. Connect Execution Ends to END
parent_builder.add_edge("call_leave_wf", END)
parent_builder.add_edge("call_kb_wf", END)
parent_builder.add_edge("call_fallback_wf", END)

# E. Compile Final Main Workflow Graph
main_askhr_workflow = parent_builder.compile()
