# DRC Harness

## Installation

Requirements: Python 3.12+, Tkinter, and access to the Gemini API.

### Clone the repo
```sh
git clone <repo-url>
cd drc-harness
```

### With `uv` (recommended)

```bash
uv sync
```

### With plain `pip`

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Tkinter is part of the standard library but is sometimes packaged separately
on Linux:

```bash
sudo apt install python3-tk
```

### Configure Gemini

```bash
cp .env.example .env
```

Edit `.env`:

```env
GEMINI_API_KEY=your_real_key
GEMINI_MODEL=gemini-3.5-flash-lite
```

#### Configuration reference

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | yes | — | Google AI Studio API key |
| `GEMINI_MODEL` | yes | — | Model that supports function calling |
| `GEMINI_SYSTEM_INSTRUCTION` | no | built-in | Override the system instruction |
| `GEMINI_TEMPERATURE` | no | SDK default | 0.0–2.0 |
| `MAX_TOOL_ITERATIONS` | no | `8` | Maximum Gemini requests per user turn |
| `MAX_TOOL_RESULT_CHARS` | no | `20000` | Truncate huge MCP results sent to Gemini |
| `MCP_CONFIRM_TOOL_CALLS` | no | `true` | Initial state of the confirmation checkbox |
| `MCP_CONFIG_PATH` | no | `mcp_servers.json` | Server configuration file |
| `LOG_LEVEL` | no | `INFO` | Python logging level |



### Configure the MCP server


`MCPClient` already accepts a list of servers. Add another entry:

```json
{
  "servers": {
    "klink": {
      "command": "/home/ig/sky130-env/bin/python",
      "args": ["-m", "klink.mcp"]
    },
    "notes": {
      "command": "/usr/bin/python3",
      "args": ["/home/ig/tools/notes_server.py"],
      "env": {"NOTES_DIR": "/home/ig/notes"},
      "cwd": "/home/ig/tools"
    }
  }
}
```

MCP server entries support:

| Key | Required | Meaning |
| --- | --- | --- |
| `transport` | no | Only `stdio` is implemented |
| `command` | yes | Executable, ideally absolute |
| `args` | no | List of command arguments |
| `env` | no | Extra environment variables merged with the SDK defaults |
| `cwd` | no | Working directory for the server process |
| `connect_timeout` | no | Seconds to wait for initialization |




---

## Run

- with uv 
```bash
uv run agentic-chatbot
```
- with pip 
```sh
python app/main.py 
```

Start KLayout with the klink plugin before asking questions that need layout
tools. The chatbot will show the MCP error if it is unavailable.

### Using the window

- **Enter** sends; **Shift+Enter** inserts a newline.
- The status area and spinner show connection/thinking activity.
- Tool calls appear in grey monospace blocks; failures appear in red.
- **Confirm tool calls** is checked by default. Each call shows the exact tool
  name and arguments before execution.
- **Clear** forgets the conversation and resets the chat display.
- If startup connection fails, fix the server and press **Send**: the agent
  retries the connection before the next turn.

---

