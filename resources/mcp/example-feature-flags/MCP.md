# Feature Flag MCP (example)

## Transport

HTTP.

## Tools

- `get_flag` — read the current state of one flag.
- `list_flags` — list flags for a service.

## Why read-only

Changing a flag changes production behaviour. That belongs behind an approval
gate, not behind a tool an agent may call freely.
