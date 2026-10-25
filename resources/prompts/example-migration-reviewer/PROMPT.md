# Migration Reviewer (example)

## Template

Review this migration as a cautious database reviewer.

Migration: {{sql}}

Report, in order:
1. Locking behaviour on hot tables.
2. Reversibility and data preservation.
3. Statements that cannot be rolled back safely.

Cite the offending statement for every finding. If nothing is wrong, say so
plainly instead of padding the list.
