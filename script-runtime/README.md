# Shared script runtime

The Python and Bash plugins are separate cards with independent workload images.
Their step classes share this package's subprocess execution, cancellation, and
output-capture implementation.

Nonzero subprocess exits remain deliberate SDK failures. Their structured
`error_result_json` includes `return_code`, `stdout`, and `stderr` so callers can
show the captured output alongside the failure. Rebuild both workload images
to pick up this behavior.
