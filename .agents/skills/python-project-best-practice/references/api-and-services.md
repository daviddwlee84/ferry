# HTTP APIs, and the skill-vs-MCP decision

Load this when the project needs to expose an HTTP surface, or when someone
asks whether to build an MCP server.

## FastAPI + Pydantic

The `api` profile generates a small, correct FastAPI app: typed request and
response models, a `/health` probe, and a lifespan that configures logging once
at startup. It exists so the project has a working HTTP surface on day one, not
as a production architecture.

What you get for free by declaring Pydantic models rather than parsing dicts:

- `/docs` (Swagger UI), `/redoc`, and `/openapi.json` generated from the code,
  so the API documentation cannot drift from the API.
- 422 responses with field-level detail on invalid input, for free.
- A schema an agent or client generator can consume directly.

The moment you need lifespan-loaded models, auth, SSE streaming, repositories,
background work, or deployment shape — **stop extending the stub** and use the
`fastapi-ai-patterns` skill (patterns and gotchas) or `fastapi-ai-scaffold` (a
full production skeleton). Those skills own this territory.

Two things to carry over regardless:

- **Never block the event loop.** CPU-bound work goes through
  `anyio.to_thread.run_sync`; a synchronous model call in an `async def` stalls
  every concurrent request on that worker.
- **Use `with TestClient(app)`**, not a bare `TestClient(app)`. The context
  manager runs the lifespan, so tests exercise the same startup path as
  production.

## Skill or MCP?

Both give an agent capability. They are not interchangeable.

| | Agent skill | MCP server |
|---|---|---|
| What it is | markdown instructions loaded into context | a running process exposing typed tools |
| Cost | context window | a process, transport, and auth to operate |
| Good at | conventions, workflows, "how we do X here" | live queries, stateful sessions, credentialed access |
| Fails at | anything needing fresh data or credentials | anything that is really just documentation |

Decision rule, in order:

1. **Can a CLI subcommand do it?** Then write the subcommand. An agent can
   already run commands, read stdout, and check exit codes — that is the
   cheapest capability you can ship, and humans get it too.
2. **Is it knowledge rather than access?** Write a skill. Conventions, layout,
   review checklists, "run these commands in this order" — all of this is text.
   Your package should ship one (see [`agent-interface.md`](agent-interface.md)).
3. **Does it need a live connection, credentials, or session state the agent
   cannot hold?** Now an MCP server earns its complexity: querying a database
   the agent has no client for, driving an authenticated API, streaming from a
   long-lived process.

Most "we need an MCP" requests are answered by rule 1. Adding a `--json` output
mode to an existing command is usually the whole feature.

When an MCP really is the answer, use the vendored `mcp-builder` skill. If you
build one in this project, it is a subcommand (`my-tool mcp serve`) rather than
a second package — same dependencies, same settings, same logging.
