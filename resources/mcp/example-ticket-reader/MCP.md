# Ticket Reader MCP (example)

## Transport

HTTP, authenticated per caller.

## Tools

- `get_ticket` — read one issue by id.
- `search_tickets` — search by label or text.

## Permissions

Issues are read-only. Creating or editing an issue is deliberately not exposed,
because write access to a tracker requires its own approval path.
