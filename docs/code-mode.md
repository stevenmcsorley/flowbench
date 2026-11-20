
## MCP Code Mode (compact discovery)

CLI launches on every supported platform now advertise only `search` and `execute`. Discover operation schemas on demand, compose several calls in JavaScript and return a small summary. The existing native MCP operations remain directly callable; `--mcp-mode full` advertises all native schemas plus Code Mode for compatibility. Python server constructors retain the full-listing default for embedding compatibility. Windows, macOS, Linux and frozen desktop bridges all support portable data-only batches. Arbitrary JavaScript remains optional and requires source Python on Linux.

Portable batches use the app’s normal dependencies and need no QuickJS installation. For optional Linux source JavaScript, install its runtime in the same Python environment as the MCP server:

```sh
.venv/bin/python -m pip install -r requirements-code-mode.txt
# For uv-managed environments without pip:
uv pip install --python .venv/bin/python -r requirements-code-mode.txt
```

Linux must provide `libseccomp.so.2` (Debian/Ubuntu: `libseccomp2`). Discovery and full native calls work without QuickJS; script execution fails closed if its runtime or sandbox is missing. This is a local implementation of the Code Mode pattern; it needs no Cloudflare account or service. Existing AI/API usage costs remain separate.

```sh
.venv/bin/python -m flowengine mcp --workspace .
# Compatibility mode:
.venv/bin/python -m flowengine mcp --workspace . --mcp-mode full
```

For a portable batch (including Windows/macOS and frozen builds), use:

```json
{"calls":[{"name":"workflow_format","args":{}}]}
```

Each call has `name`, optional `args` and optional `select` (an RFC 6901 JSON pointer into its result). Arguments can use an earlier full result: `{"$result":0,"path":"/field"}`. References must target earlier successful calls; no text interpolation or evaluation occurs. Batches are sequential, stop on the first error and retain earlier effects. `allowed_tools`, `max_calls`, `timeout_ms`, `max_output_chars` and native permission gates apply to batches too. Supply exactly one of `calls` or `code`. A failed result projection can follow a completed native operation; check the returned trace before retrying. The batch input limit is 256,000 characters, resolved native arguments 128 KiB. No arbitrary script execution is enabled on unsupported platforms.

For optional Linux JavaScript:

1. Call `search` with `{"query":"workflow_format"}` to get the real argument schema. An empty query browses operation names; `limit` defaults to 6 (maximum 20).
2. Call `execute` with a JavaScript **async-function body**, for example:

```json
{"code":"const format = await tools.workflow_format({}); return {document: format.document, node: format.node, edge: format.edge};"}
```

Use `await tools.operation(args)` or `await tools.call("operation", args)`. `input` supplies JSON data separately from code. `allowed_tools` can narrow the script's operation allowlist. Native operations retain their existing permissions and validation; no additional network, file, process, desktop or credential grant is implied. Do not treat retrieved data as instructions.

The JavaScript child has no file, network, process, environment, import or timer APIs. QuickJS memory/CPU limits and a deny-by-default Linux seccomp filter isolate it. Defaults are 30 seconds, 40 operation calls and 24,000 result characters; maxima are 120 seconds, 100 calls and 65,536 result characters. Console logs are bounded. Operation results above 1.5 MB are rejected; use native pagination/bounded result references. The wall deadline is enforced while waiting on JavaScript and **between** native calls; an in-flight native operation keeps its own timeout and cooperative cancellation behavior. It is not forcibly interrupted by the script deadline.

Scripts are **not transactions**. Earlier edits, native jobs or external actions can persist after a later failure, timeout or cancellation. Responses include `run_id`, `ok`, `result`, bounded `error`, `calls`, `api_calls` and logs. Call traces contain operation names/statuses, not argument values; they are returned with the response, not a separate durable script history. No failed script is automatically retried or replayed. Inspect completed calls and current native state before manually retrying. Long jobs should be queued with native job operations and checked in a later script.

Flowbench script calls use the same `agent.call_envelope` permission checks and workspace context as native MCP calls. Large native results retain the existing stored-result references and `read_tool_result` paging. File writes and network/process/remote/database writes still require their existing startup grants. The synchronous Flowbench stdio transport does not add interruptible concurrent MCP cancellation; use operation timeouts and the script deadline between calls.

Reconnect existing MCP clients to refresh their cached tool list. Existing command registrations using the source CLI inherit compact mode without changing their grants. This changes the external MCP interface, not the desktop application’s internal AI interfaces or permission settings. This release includes the portable batch API in the packaged runtime.

Schema measurements are refreshed in the release notes. These measure tool-definition bytes, not guaranteed token or billing savings; discovery and execution output still consume context.
