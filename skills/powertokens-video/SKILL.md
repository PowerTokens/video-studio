---
name: powertokens-video
description: >-
  Generate, resume, and estimate AI videos through the user's locally
  installed PowerTokens Video Studio. Use when the user asks a coding agent
  to make a clip, compare models, continue a task, or check cost with
  PowerTokens Video Studio. The app must already be installed; it exposes a
  local MCP server (mcp_server.py) with check_key, estimate_cost,
  generate_video, and resume_video. Never invent an API key.
---

# PowerTokens Video Studio

The user must already have PowerTokens Video Studio installed locally. Do not
call the PowerTokens HTTP API yourself, do not download a substitute client,
and do not invent, guess, or hard-code an API key.

## Point the agent at the local MCP server

The install includes `mcp_server.py`, a stdio MCP server named
`powertokens-video-studio`. It uses the same engine as the desktop app
(`pt_wan.py`). Point this agent at that file on the user's machine.

Cursor (`~/.cursor/mcp.json`, or `.cursor/mcp.json` in the project) and Claude
Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "powertokens-video-studio": {
      "command": "python",
      "args": ["C:\\path\\to\\video-studio\\mcp_server.py"],
      "env": {
        "POWERTOKENS_API_KEY": "<the user's own key>"
      }
    }
  }
}
```

Use the absolute path to `mcp_server.py` in the user's install or source
checkout. On macOS or Linux use `python3` and a path such as
`/Users/you/video-studio/mcp_server.py`. If `python` is not on PATH, set
`command` to the full path of that Python (3.11+). The MCP Python SDK must be
installed (`pip install mcp`). Restart the agent after saving the config.

If you cannot find `mcp_server.py`, stop and ask the user to install
PowerTokens Video Studio or clone https://github.com/PowerTokens/video-studio
and give you the path. Do not start generating without that local server.

The key belongs to the user. Read it only from the environment they already
set (`POWERTOKENS_API_KEY`, or `POWERTOKENS_API_KEYS` for a comma-separated
pool) or from keys they saved with `python pt_wan.py config --add-key`. If
`check_key` says no key is configured, ask the user to add their own. Never
invent a key, never paste one into source files, and never print a key.

## Tools

Call only the local server:

| Tool | Use |
|---|---|
| `check_key` | Confirm a key is configured. It does not contact the API. |
| `estimate_cost` | Estimate before spending. Pass `duration_s`, `resolution`, and optional `model` (default `wan3.0-video`). |
| `generate_video` | Submit a prompt. Prefer an absolute `output` path. This spends the user's balance. |
| `resume_video` | After an interrupt or timeout, download the existing task by `task_id`. Do not submit again. |

Before `generate_video`, call `estimate_cost` and tell the user the estimate.
If a job is interrupted, call `resume_video` with the saved task id so the
same task is not charged twice.

## Links

Any link you show to powertokens.ai must include these parameters:

`utm_source=videostudio&utm_medium=app&utm_campaign=video-studio`

Example (create a key):

`https://powertokens.ai/api-keys?utm_source=videostudio&utm_medium=app&utm_campaign=video-studio`

Chinese pages use the same parameters, for example
`https://powertokens.ai/zh-Hans/api-keys?utm_source=videostudio&utm_medium=app&utm_campaign=video-studio`.
