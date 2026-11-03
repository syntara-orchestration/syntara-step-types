# Python Script

Runs Python source in an isolated subprocess and captures its output. The final
non-empty stdout line is parsed as JSON when it contains valid JSON.

## Inputs

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `code` | string | Yes | — | Python source code |
| `environment` | object | No | `{}` | Extra environment variables |
| `max_output_bytes` | integer | No | `276480` | Bytes captured per stream before truncation (1–1048576) |

## Outputs

| Field | Type | Description |
| --- | --- | --- |
| `return_code` | integer | Process exit code (`0` = success) |
| `stdout` | string | Standard output |
| `stderr` | string | Standard error |
| `stdout_json` | any | Parsed JSON from stdout, when valid |

## Build

From the repository root:

```bash
make node-image NODE=script-python CONTAINER_ENGINE=docker TAG=local-test
```
