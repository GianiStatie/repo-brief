"""The MCP server and `ask` for one repository.

`settings`, `tools`, `prompt`, `result` and `ask` use the standard library
only. `server` needs `repo-brief[mcp]`, and each runner its own CLI's library
(`repo-brief[claude]` for the Claude runner); nothing imports those until
`repo-brief serve` or `repo-brief ask` runs.
"""
