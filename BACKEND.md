# SLTMobitel Ask AI - Backend Domain Architecture

## 1. System Overview
This document outlines the LangGraph-based multi-agent architecture for the Ask SLT backend. The system routes user queries to 6 specialized agents based on the `agent_id` provided by the frontend. 

The architecture uses a **Registry Pattern** combined with **LangGraph** to easily scale and add new agents without duplicating core logic.

## 2. The 6 Agents & Their Archetypes
The system handles three distinct levels of agent complexity (Archetypes):

### Archetype 1: Knowledge Base (KB) Only
* **Agents:** Ask Finance, Ask Admin, Ask Process
* **Behavior:** Directly embeds the user query, searches the specific Qdrant collection (e.g., `finance_docs`), and generates a response. No complex routing required.

### Archetype 2: KB + API (with Supervisor)
* **Agent:** Ask HR
* **Behavior:** Requires an LLM Supervisor Node for intent classification.
    * *Intent A:* General policy query -> Route to `rag_tool` (searches `hr_docs`).
    * *Intent B:* Personal data query (e.g., leave balance) -> Route to `api_tool` (queries SQL DB).

### Archetype 3: KB + Form (State Machine)
* **Agents:** Ask Lifestore, Ask Enterprise
* **Behavior:** Requires slot-filling state management.
    * *Intent A:* Product information -> Route to `rag_tool`.
    * *Intent B:* Transaction/Request -> Triggers `form_tool` to collect missing mandatory slots (e.g., package type, customer ID) before executing.

---

## 3. Directory Structure
```text
backend/domain/
│
├── state.py                # Defines AgentState (messages, user_id, active_agent, form_slots)
├── tools/                  # Reusable tools for agents
│   ├── __init__.py
│   ├── rag_tools.py        # Qdrant search tool (Uses gemini-embedding-001 - 3072 dims)
│   ├── api_tools.py        # SQL/External API calls
│   └── form_tools.py       # Slot-filling and validation logic
│
├── archetypes/             # LangGraph blueprints
│   ├── __init__.py
│   ├── kb_agent.py         # Graph for Archetype 1
│   ├── kb_api_agent.py     # Graph for Archetype 2 (Includes Supervisor Node)
│   └── kb_form_agent.py    # Graph for Archetype 3
│
└── registry.py             # Maps 'agent_id' from frontend to the correct Archetype Graph