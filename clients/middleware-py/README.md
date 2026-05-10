# mnemo-middleware

Drop [mnemo](https://github.com/mmct-jsc/mnemo) retrieval into any
LLM SDK call. Works with OpenAI, Anthropic, Google (Gemini), and
Ollama clients today.

## Install

```bash
pip install mnemo-middleware           # core
pip install 'mnemo-middleware[openai]' # core + OpenAI shim
pip install 'mnemo-middleware[anthropic,openai,google,ollama]' # everything
```

A running mnemo daemon is required: `mnemo daemon start` (see the
[main repo](https://github.com/mmct-jsc/mnemo)).

## Two ways to use

### 1. Manual: `retrieve_context()` helper

You ask, you inject:

```python
from openai import OpenAI
import mnemo_middleware as mm

client = OpenAI()
ctx = mm.retrieve_context("how do we handle MQTT auth?")

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[
        {"role": "system", "content": ctx},
        {"role": "user", "content": "how do we handle MQTT auth?"},
    ],
)
```

If the daemon is down or slow, `retrieve_context()` returns `""` and
logs a warning. The model call always proceeds.

### 2. Auto-patch: one-line setup

Patch the SDK client; mnemo injects retrieval as a system message
on every chat call:

```python
from openai import OpenAI
import mnemo_middleware as mm

client = OpenAI()
mm.patch(client, mode="auto")  # default

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "how do we handle MQTT auth?"}],
)  # mnemo block prepended automatically
```

Three injection modes:

| Mode | When mnemo injects | Use for |
|---|---|---|
| `auto` (default) | Turn 1, new conversation prefix, or topic shift detected | Multi-turn chats |
| `once` | First call after `patch()` only | Persistent agents that run for hours |
| `every` | Every single call | One-shot evaluators / batch processing |

Reverse with `mm.unpatch(client)`.

## Configuration (env vars)

| Variable | Default | What |
|---|---|---|
| `MNEMO_DAEMON_URL` | `http://127.0.0.1:7373` | Where the daemon listens |
| `MNEMO_DEFAULT_BUDGET` | `800` | Max tokens per retrieval block |
| `MNEMO_DEFAULT_K` | `5` | Number of hits to request |
| `MNEMO_TIMEOUT` | `2.0` | Per-call seconds before giving up |

## Failure mode

The middleware is **always additive**:

- Daemon down → empty injection, model call proceeds.
- Daemon timeout → empty injection, model call proceeds.
- Provider SDK breaking change → patch raises `UnsupportedClient`
  cleanly; you can call `unpatch()` and use the helper instead.

A slow or missing mnemo never blocks your model call indefinitely.

## License

MIT.
