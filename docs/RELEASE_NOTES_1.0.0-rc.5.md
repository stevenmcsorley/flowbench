# Flowbench 1.0.0-rc.5

## Compact MCP discovery on every platform

- External MCP clients now discover two compact tools, `search` and `execute`, instead of loading every operation schema up front.
- `search` returns matching native operation schemas on demand. `execute` supports sequential data-only batches on Windows, macOS, Linux and packaged runtimes. Batches can reference earlier results, select fields with JSON pointers and return bounded summaries.
- Native permissions, validation and workspace/project boundaries still apply. Full native calls remain compatible; start with `--mcp-mode full` to advertise every native schema.
- Optional Linux source builds support isolated JavaScript composition through QuickJS-NG and seccomp. JavaScript is not enabled in frozen bridges, Windows or macOS; use portable batches there. No Cloudflare account or service is needed.
- Operations are not atomic: earlier actions can remain after a failure, cancellation or deadline. Responses include operation names/statuses; scripts and batches never automatically replay. In-flight native calls retain their own timeouts.

## Packages

Windows x64 installer, Linux x64 Debian/AppImage packages, and macOS Apple Silicon and Intel disk images are built from the release tag. Installers are currently unsigned. On macOS, use the system's documented Open Anyway flow if Gatekeeper blocks the downloaded application.

## Validation and scope

- Portable contracts run on Windows, Linux, macOS Apple Silicon and macOS Intel; packaged-runtime acceptance verifies real stdio discovery and a two-operation batch.
- Linux sandbox tests cover file/socket denial, call/output/CPU limits, permission failures and partial completion. Platform packaging and existing application tests run in GitHub Actions.
- The desktop application's internal AI tools and permission settings remain unchanged. Reconnect external MCP clients to refresh their cached tool list.
- Measured initial tool-definition JSON: **2,110 UTF-8 bytes** in compact mode versus **24,283 bytes** in full mode (**91.3% smaller**, 37 native operations). These are schema-byte measurements, not guaranteed token or billing savings; discovery and output also consume context.

See [Code Mode documentation](https://github.com/stevenmcsorley/flowbench/blob/main/docs/code-mode.md) for setup, examples and limitations.
