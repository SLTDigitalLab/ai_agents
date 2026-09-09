"""
Everything the helpdesk_dev agent's pipeline logic needs lives under this
package:
  pipeline/  one file per graph stage — [NODE]/[ROUTER] functions and the
             category-classification pipeline. Wired into the actual
             LangGraph StateGraph by domain/archetypes/helpdesk_agent.py.
  prompts/   system prompts, split to match the pipeline stages
  tools/     @tool definitions + retrieval helpers the pipeline calls

See domain/archetypes/helpdesk_agent.py for build_helpdesk_workflow() and
the overall conversation flow.
"""
