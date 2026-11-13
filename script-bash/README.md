# Bash Script

Runs Bash source in an isolated subprocess and captures its output.

## Inputs

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `code` | string | Yes | — | Bash source code |
| `environment` | object | No | `{}` | Extra environment variables |
| `max_output_bytes` | integer | No | `276480` | Bytes captured per stream before truncation (1–1048576) |

## Outputs

| Field | Type | Description |
| --- | --- | --- |
| `return_code` | integer | Process exit code (`0` = success) |
| `stdout` | string | Standard output |
| `stderr` | string | Standard error |

## Build

From the repository root:

```bash
make node-image NODE=script-bash TAG=local-test
```

The SDK build creates the workload OCI archive; Make loads it into the selected
local container engine as `localhost/syntara-node-script-bash:local-test`.
