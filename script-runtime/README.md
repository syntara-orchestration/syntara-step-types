# Shared script runtime

The Python and Bash plugins are separate cards with independent workload images.
Their step classes share this package's subprocess execution, cancellation, and
output-capture implementation.
