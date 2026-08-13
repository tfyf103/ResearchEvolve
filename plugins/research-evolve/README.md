# ResearchEvolve Codex Plugin

This local plugin exposes ResearchEvolve v1.2 through five workflow Skills and a workspace-confined stdio MCP server. Discovery, proof, and formalization use isolated Codex-native actors by default. Every role runs in a fresh ephemeral `codex exec` process with projected context, structured output, read-only sandboxing, no approvals, and no inherited MCP/plugin/web/subagent tools. The server never grants trusted research statuses; certification remains in the existing Core and Lean gates.

Install from the repository root:

```bash
python -m pip install -e ".[plugin]"
research-evolve-plugin --root . doctor
```

Then install this repository marketplace in Codex, or load `plugins/research-evolve` as a local plugin. The MCP server command is `research-evolve-mcp --root .`.

The plugin deliberately has no UI. `actor_backend=manual` retains the v1.1 journaled task exchange only as a disclosed diagnostic fallback. Use actor run audits to inspect isolation fingerprints and failures.
