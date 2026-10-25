# Test Coverage Gap Analysis (example)

## Steps

1. List the changed functions and the branches they add.
2. Map each branch to a test that reaches it.
3. Report branches with no reaching test, with file and line.
4. Separate "newly added and uncovered" from "already uncovered".

## Warning

A covered line is not a correct assertion. Report coverage as a map of what is
executed, never as evidence that behaviour is right.
