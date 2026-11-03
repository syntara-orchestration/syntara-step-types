# Runtime dependency layout

The step workload images install `syntara-plugin-contracts`, which provides the
runtime APIs used by the step implementations and their `ContainerService` entrypoints.
The `script` and `http-request` package metadata each declare contracts as a direct
runtime dependency, and their Containerfiles pin the image dependency through
`CONTRACTS_VERSION`.

The repository also uses `syntara-plugin-sdk` in its development dependency group.
Tests use its plugin settings and compilation APIs (`load_settings`, `BuildRequest`,
and `compile_workspace`) to validate the step descriptors and their runtime bindings.
Those authoring APIs are not imported by the step workload source.

`uv.lock` records both packages for development. Container builds install only the
contracts package and the step package, keeping the plugin authoring tools out of the
workload images.
