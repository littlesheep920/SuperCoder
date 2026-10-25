# Dependency Upgrade Plan (example)

## Steps

1. Record the current and target versions.
2. List breaking changes from the changelog that could reach this code.
3. For each, name the call site that would need to change.
4. Propose an order: mechanical changes first, behavioural changes last.

## Limit

A changelog is not proof of compatibility. Require a green test run before
recommending the upgrade be kept.
