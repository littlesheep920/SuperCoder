# Demo Deterministic Text Statistics Tool

> **Demo resource:** This is a runnable Stage 0 protocol example, not a published, enterprise-approved, security-certified, or production-ready asset.

## Purpose

The tool demonstrates a complete deterministic `tool` resource with a machine-readable manifest, a standard-library Python entrypoint, typed JSON input and output, stable validation failures, and no network access. It computes statistics only for text supplied directly by the caller; it does not open files, inspect environment variables, invoke an LLM, start a server, or launch child processes.

## Files

- `resource.yaml` is the JSON-compatible YAML manifest and points to `tool.py`.
- `tool.py` exposes `calculate_text_stats(request)` for in-process use and a CLI for JSON-over-stdio or `--input-json` use.
- `README.md` documents behavior and acceptance limits.

## Input contract

The input is one JSON object with exactly one required property:

```json
{"text": "One two\n三"}
```

`text` must be a string containing at most 1,000,000 Unicode code points and only Unicode scalar values, so it can be encoded as strict UTF-8. Unknown properties, missing `text`, non-string values, malformed JSON, non-object JSON, unpaired UTF-16 surrogate code points, and oversized input are rejected with the structured `INVALID_ARGUMENT` error. The implementation does not coerce values or silently discard fields.

## Success output

A successful invocation writes exactly one JSON object to standard output:

```json
{
  "character_count": 9,
  "line_count": 2,
  "non_whitespace_character_count": 7,
  "utf8_byte_count": 11,
  "word_count": 3
}
```

The fields have these deterministic meanings:

- `character_count`: number of Python Unicode code points (`len(text)`).
- `non_whitespace_character_count`: code points for which `str.isspace()` is false.
- `word_count`: number of non-empty runs separated by Unicode whitespace (`text.split()`).
- `line_count`: number of logical lines recognized by `str.splitlines()`; empty text has zero lines and a trailing line separator does not add an extra empty line.
- `utf8_byte_count`: number of bytes in strict UTF-8 encoding.

For the same Python Unicode semantics and the same input object, the result is equivalent and independent of locale, time, random state, filesystem contents, environment variables, or network state.

## CLI

From this resource directory, pass JSON directly:

```powershell
python tool.py --input-json '{"text":"One two\n三"}'
```

Or provide one JSON object through standard input:

```powershell
'{"text":"alpha beta"}' | python tool.py
```

The process exits with code `0` after writing a success object to standard output. Invalid input exits with code `2`, writes no success object, and writes an ASCII-safe structured error to standard error (non-ASCII error details are JSON-escaped so even malformed surrogate input cannot trigger an output-encoding traceback):

```json
{"error":{"code":"INVALID_ARGUMENT","message":"missing required property: text","retryable":false}}
```

The CLI does not read input paths. Runtimes must launch it without a shell, use this resource directory as the working directory, enforce the manifest timeout and output limits, and validate both request and success result against the schemas in `resource.yaml`.

## Security and acceptance boundary

The checked-in implementation imports only Python standard-library modules and contains no socket, HTTP, URL, file-open, subprocess, dynamic-code, credential, or model-invocation path. The manifest explicitly denies network and project filesystem access. Those declarations do not grant or enforce a sandbox by themselves; a production executor must still impose process, memory, time, input-size, output-size, filesystem, and network controls.

Stage 0 acceptance is limited to manifest parseability and schema conformance, entrypoint presence, Python compilation, deterministic local smoke execution, and documented input/output behavior. It does not prove compatibility with every declared runtime, resistance to hostile operating-system conditions, production performance, or governance approval.
