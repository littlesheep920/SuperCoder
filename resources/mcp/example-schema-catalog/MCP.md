# Schema Catalog MCP (example)

## Transport

HTTP.

## Tools

- `list_tables` — list tables in a schema.
- `describe_table` — return columns, types, and nullability.

## Permission boundary

Metadata only. Row-level data is explicitly out of scope: a schema catalog is not
a query engine, and exposing row data would require a separate review.
