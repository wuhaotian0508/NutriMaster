# Pi Tool Contract

`nutrimaster.agent.pi_tools.PiToolService` is the Python-owned boundary for
Pi tools. It has no FastAPI or Node dependency:

```python
payload = await PiToolService(services.registry).execute(
    "rag_search",
    model_arguments,
    PiToolContext(user_id=user.id, include_personal=use_personal, mode="normal"),
)
```

Create a `PiToolService` once per Pi agent run, not as a process-wide
singleton. The instance owns the citation registry that de-duplicates papers
and keeps source IDs stable across repeated `rag_search` calls in that run.

The caller must construct `PiToolContext` from authenticated request state.
Fields named `user_id`, `include_personal`, and `mode` in model arguments are
ignored.

## Tool schemas

Use `PI_TOOL_SCHEMAS` for the Pi extension parameter definitions. These are
the only fields the model may supply.

- `rag_search`: `query` is required; optional fields are `pubmed_query`,
  `gene_db_query`, `gene_db_keyword_spec`, `focus`, and positive `top_k`.
- `experiment_design`: `experiment_type` (`crispr` or `gene_transfer`) and
  `goal` are required; optional fields are `genes`, `output`, and `confirmed`.

## Result payload

Every call returns a JSON-serializable object with `tool`, `ok`, `tool_text`,
`summary`, `citations`, `graph_evidence`, `source_counts`, and `warnings`.
Experiment calls additionally contain `stage`: `preview` unless the caller
requested `output="full_sop"` with `confirmed=true`, then `complete`.

When `ok` is false, `error` is `{ "code", "message" }`. Stable codes are:

- `unknown_tool`
- `invalid_context`
- `invalid_arguments`
- `invalid_tool_result`
- `tool_execution_failed`

`tool_execution_failed` deliberately has no underlying exception text; the
Python service logs the diagnostic server-side.
