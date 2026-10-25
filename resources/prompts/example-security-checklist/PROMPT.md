# Security Checklist (example)

## Template

Diff: {{diff}}

Check, and report only what the diff actually shows:
1. Untrusted input reaching a sink (query, shell, path, template).
2. Credentials or secrets written into code, logs, or test data.
3. Authorization checks removed or weakened.
4. New outbound network destinations.

For each finding, quote the line. Absence of evidence is not evidence of safety:
state which categories you could not assess.
