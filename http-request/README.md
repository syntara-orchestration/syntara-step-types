# http-request step

Makes an outbound HTTP request from a container step. Returns the response status,
body, headers, and elapsed time. SSRF protection rejects private and loopback IPs.

## Inputs

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `url` | string | Yes | — | Target URL (`http://` or `https://` only) |
| `method` | string | No | `GET` | HTTP method |
| `headers` | object | No | `{}` | Request headers |
| `body` | object\|string | No | `null` | Request body |
| `query_params` | object | No | `{}` | Query string parameters |
| `timeout_seconds` | integer | No | `30` | Request timeout (1–3600) |
| `follow_redirects` | boolean | No | `false` | Follow HTTP redirects |

## Outputs

| Field | Type | Description |
| --- | --- | --- |
| `status_code` | integer | HTTP response status code |
| `body` | any | Parsed JSON body, or raw text |
| `headers` | object | Response headers |
| `elapsed` | number | Request duration in seconds |

## Build

From the repository root:

```bash
make node-image NODE=http-request CONTAINER_ENGINE=docker TAG=local-test
```

Build context must be the repository root so the workspace packages resolve correctly.

## Example inputs (`example.json`)

```json
{
  "url": "https://httpbin.org/get",
  "method": "GET",
  "timeout_seconds": 30
}
```

See [`docs/runtime.md`](../docs/runtime.md) for the gRPC ABI and deployment details.
