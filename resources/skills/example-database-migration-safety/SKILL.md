# Database Migration Safety (example)

## When to use

Before applying a migration to a shared database.

## Checks

1. Does the migration take an exclusive lock on a table that receives traffic?
2. Is there a rollback path, and does it preserve data?
3. Are columns dropped, retyped, or narrowed?
4. Is the change reversible without a backup restore?

## Reporting

State each risk with the table, the operation, and the expected lock duration.
Never execute the migration as part of this check.
