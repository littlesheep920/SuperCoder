# Example Cache Invalidation Review

## When to use

Reviews cache invalidation rules for stale-read windows and missed invalidations.

## Steps

1. Collect the declared input.
2. List every write path, then check that each one invalidates what it changed.
3. Report each finding with the exact item it refers to.

## Limits

Illustrative sample content. Do not act on the output without a human decision.
