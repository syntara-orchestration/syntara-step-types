# script step

Runs Python or Bash code in an isolated subprocess. Captures stdout, stderr, and
exit code. For Python scripts, the last line of stdout is parsed as JSON if valid.

## Inputs

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `language` | `python`\|`bash` | Yes | `python` | Script language |
| `code` | string | Yes | — | Script source code |
| `environment` | object | No | `{}` | Extra environment variables |

## Outputs

| Field | Type | Description |
| --- | --- | --- |
| `return_code` | integer | Process exit code (`0` = success) |
| `stdout` | string | Standard output |
| `stderr` | string | Standard error |
| `stdout_json` | any | Parsed JSON from stdout (Python scripts only) |

## Failures

- Non-zero exit code → `REMOTE_REJECTED` (not retryable)
- Timeout → `TIMEOUT` (retryable)

## Build

From the repository root:

```bash
make node-image NODE=script CONTAINER_ENGINE=docker TAG=local-test
```

Build context must be the repository root so the workspace packages resolve correctly.

## Example inputs (`example.json`)

```json
{
  "language": "python",
  "code": "import json\nprint(json.dumps({'result': 42}))",
  "environment": {}
}
```

See [`docs/runtime.md`](../docs/runtime.md) for the gRPC ABI and deployment details.
