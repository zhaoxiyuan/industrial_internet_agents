# LangGraph Callback Example

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

export AGENT_CALLBACK_TOKEN='replace-callback-token'
uvicorn app:app --host 127.0.0.1 --port 8000
```

Start the Gateway with `config/config.callback.example.json`. The included graph is an echo graph so the example works without any model API key. Replace `agent_node` with the actual LangGraph node or compiled graph.

The in-memory checkpointer and event cache are for demonstration only. Use persistent storage in production.
