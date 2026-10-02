"""Route documentation built from docs/api (the single source).

`route_doc(method, path)` returns the keyword arguments for a FastAPI route
decorator: summary, the full endpoint section as description, the complete text
of every agent rule that applies to it, and the `x-agent-rules` / `x-status`
OpenAPI extensions. Implemented routes and documented-but-planned stubs use it
alike, so Swagger (/docs), /v1/openapi.json and /v1/docs/endpoint all carry the
same documentation and rules.
"""

from __future__ import annotations

from src.api import docs_loader

_RULES_URL = "/v1/agent-rules"


def _rules_by_id() -> dict[str, docs_loader.AgentRule]:
    return {r.id: r for r in docs_loader.load_agent_rules()}


def rules_markdown(rule_ids: tuple[str, ...]) -> str:
    by_id = _rules_by_id()
    lines = [f"### Agent rules for this endpoint (full list: [{_RULES_URL}]({_RULES_URL}))", ""]
    for rid in rule_ids:
        rule = by_id.get(rid)
        if rule:
            why = f" — *why*: {rule.why}" if rule.why else ""
            lines.append(f"- **{rule.id}** ({rule.level}): {rule.text}{why}")
    return "\n".join(lines)


def endpoint_docs(method: str, path: str) -> list[docs_loader.EndpointDoc]:
    return docs_loader.load_endpoint_docs().get((method.upper(), path), [])


def route_doc(method: str, path: str) -> dict:
    """kwargs for @router.<method>(…): summary, description, tags, openapi_extra."""
    docs = endpoint_docs(method, path)
    if not docs:
        raise KeyError(f"{method} {path} is not documented in docs/api/endpoints — document it first")
    main = next((d for d in docs if d.variant is None), docs[0])
    rule_ids = tuple(dict.fromkeys(r for d in docs for r in d.rules))
    body = "\n\n---\n\n".join(d.markdown.split("\n", 1)[1].strip() if "\n" in d.markdown else "" for d in docs)
    description = f"{body}\n\n{rules_markdown(rule_ids)}"
    return {
        "summary": main.summary,
        "description": description,
        "tags": [main.group],
        "openapi_extra": {
            "x-status": main.status,
            "x-status-text": main.status_text,
            "x-scope": main.scope,
            "x-mode": main.mode,
            "x-agent-rules": list(rule_ids),
            "x-docs": f"/v1/docs/endpoint?method={method.upper()}&path={path}",
        },
    }


def api_description() -> str:
    """info.description of the OpenAPI document."""
    return (
        "Evidence-grounded access to the GeoHydroAI corpus (flood, hydrology and remote-sensing papers). "
        "PostgreSQL is the layer of truth; Neo4j, Chroma and Parquet are projections.\n\n"
        "**AI agents must read the rules first**: "
        f"[`GET {_RULES_URL}`]({_RULES_URL}) (JSON) or [`?format=markdown`]({_RULES_URL}?format=markdown). "
        "Every endpoint below lists the rules that apply to it (`x-agent-rules`), and every response "
        f"carries `Link: <{_RULES_URL}>; rel=\"agent-rules\"`.\n\n"
        "### The five rules that matter most\n\n"
        f"{docs_loader.top_rules_markdown()}\n\n"
        "Endpoints whose `x-status` is `planned` are documented but answer `501 NOT_IMPLEMENTED`; "
        "check `GET /v1/capabilities`. Machine index: `GET /llms.txt`, `GET /v1/docs/index`."
    )
