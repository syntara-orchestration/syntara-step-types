# http-request step

Makes an outbound HTTP request from a container step. Returns the response status,
body, headers, and elapsed time. SSRF protection validates all destination IPs
(including hostname resolution and redirect targets) and blocks private, loopback,
link-local, and reserved addresses. DNS pinning happens at the transport layer so
redirects and cookies retain their logical hostnames. SDK cancellation stops
in-flight requests and releases their execution capacity.

## Inputs

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `url` | string | Yes | — | Target URL (`http://` or `https://` only; SSRF-protected) |
| `method` | string | No | `GET` | HTTP method |
| `credential_name` | string | No | — | Declared logical bearer credential name. Requires the trusted host runtime to supply the selected integration's HTTPS base origin as `httpEndpoint`; requests fail before credential access if it is missing or does not match the request origin. The credential's `token` field replaces any caller-supplied Authorization header, case-insensitively. |
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
make node-image NODE=http-request TAG=local-test
```

The SDK build creates the workload OCI archive; Make loads it into the selected
local container engine as `localhost/syntara-node-http-request:local-test`. The
plugin's build context is the repository root so workspace packages resolve
correctly.

## Example inputs (`example.json`)

```json
{
  "url": "https://httpbin.org/get",
  "method": "GET",
  "timeout_seconds": 30
}
```

See [`docs/runtime.md`](../docs/runtime.md) for the gRPC ABI and deployment details.

### Hermetic image builds

Konflux prefetches the hashed runtime source distributions in `requirements-konflux.txt` for
Python 3.12 on amd64 and arm64 before building with networking disabled.
Build backends are prefetched from `requirements-build.txt`, and native toolchains
come from the shared RPM lockfile. The Containerfile uses Hermeto's `PIP_FIND_LINKS` and disables index access when that
cache is present. Local builds without the cache use the public package index.
The step source and manifest are copied directly. Dependency source builds use
only prefetched build backends and toolchains.

After changing runtime dependencies, regenerate the requirements from the root:

```sh
uv export --frozen --offline --package syntara-node-http-request --no-dev --no-emit-workspace --output-file http-request/requirements-konflux.txt
```

The contracts version comes from `uv.lock`; update the lock and regenerate this
file to change it. Then run `make sync-http-build-requirements` to refresh
the hashed build dependencies. Changes to native toolchains also require
`make sync-rpms-lock-yaml`.
