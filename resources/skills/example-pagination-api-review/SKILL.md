# Example Pagination API Review

## When to use

Checks a paginated endpoint for stable ordering and cursor correctness.

## Steps

1. Collect the declared input.
2. Verify the sort key is unique, then confirm the cursor cannot skip or repeat rows.
3. Report each finding with the exact item it refers to.

## Limits

Illustrative sample content. Do not act on the output without a human decision.
