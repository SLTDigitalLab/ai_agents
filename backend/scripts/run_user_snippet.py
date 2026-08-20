"""Run the user's OpenAI-style snippet against the Ollama gateway.

Usage: run with the workspace venv Python.
"""
from openai import OpenAI

client = OpenAI(
    base_url="https://sltollama.duckdns.org/devapi/v1",
    api_key="e15f1a435125d219595535b17ac236dbc98a032451a4c924",
    timeout=30.0,
)

response = client.chat.completions.create(
    model="llama3.2:1b",
    messages=[{"role": "user", "content": "your agent's prompt here"}],
)

choice = response.choices[0] if getattr(response, 'choices', None) else None
content = getattr(choice.message, 'content', None) if choice else None
print('--- MODEL RESPONSE ---')
print(content)
print('\n--- META ---')
print('response_id:', getattr(response, 'id', None))
