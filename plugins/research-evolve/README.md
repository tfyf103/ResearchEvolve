# ResearchEvolve Codex Plugin

This local plugin exposes ResearchEvolve v1.1 through five workflow Skills and a workspace-confined stdio MCP server. The server never calls an LLM and never grants trusted research statuses; it validates inputs and delegates certification to the existing Core and Lean gates.

Install from the repository root:

```bash
python -m pip install -e ".[plugin]"
research-evolve-plugin --root . doctor
```

Then install this repository marketplace in Codex, or load `plugins/research-evolve` as a local plugin. The MCP server command is `research-evolve-mcp --root .`.

The MVP deliberately has no UI. Discovery, proof, and formalization jobs use the existing Core pipelines; Codex actor inputs and outputs cross a schema-validated, journaled bridge.
