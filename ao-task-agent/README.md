# AO Task Agent

This private SDK Action runs the agent's message and tool decision loop in one
isolated workload. It has no model or MCP client. The SDK Toolbox asks the host
for each model turn and selected MCP tool call.

AO prepares the ordered messages and selected tool definitions before dispatch.
AO owns provider credentials, integration credentials, network access, audit,
usage, and the persisted invocation result. The workload receives extracted
file context as message text and does not fetch uploaded files.

The workload returns the final content, the model calls' provider usage, and a
bounded tool trace. It does not persist intermediate state or retry an uncertain
host operation. AO treats a failed invocation as terminal for this first
iteration.

The host grants `agent.model.turn` and `agent.mcp.call` to this action. The
container entry point only advertises and executes the step handler.
