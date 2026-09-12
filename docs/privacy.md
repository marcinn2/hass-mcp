# Privacy and Personal Data

Hass-MCP reads your Home Assistant instance and makes that data available to an
LLM. Much of what Home Assistant holds is personal data, so this page sets out
what the server touches, where it goes, what it keeps, and how to limit all
three.

!!! note "Who this is for"
    You run this server, so you decide what it may read and where that data
    goes. This page is reference material for those decisions — it is not legal
    advice. If you operate Hass-MCP on behalf of other people, or in an
    organisation, take proper advice on your own obligations.

## What counts as personal data here

Hass-MCP holds no user accounts and collects nothing about you. Everything
sensitive it handles comes **from your Home Assistant instance**, and how
sensitive that is depends entirely on what you have set up:

| Data | Where it comes from | Why it matters |
|---|---|---|
| Presence and location | `device_tracker.*`, `person.*`, zones (with coordinates) | Reveals where people are and when they are home |
| Behaviour over time | `get_logbook`, `get_statistics`, history, `analyze_usage_patterns` | Daily routines, sleep and absence patterns |
| Names and rooms | `friendly_name`, area names | Often contain real names ("Anna's Bedroom") |
| Calendar entries | `get_calendar_events` | Event titles can reveal health, religion or relationships |
| Media and cameras | `media_player.*`, `camera.*` | Viewing habits; camera entity metadata |

Nothing here is special-category data *by design*, but calendar titles and
long-run presence data can easily become sensitive in practice. Worth a thought
before pointing an LLM at a household instance.

## Where data goes

### Always: your MCP client and its model

The server's purpose is to answer an LLM's questions about your home. Anything
a tool returns is sent to whatever MCP client you connected — and on to that
client's model provider. **This is the largest data flow and it is inherent to
using the project at all.** Consult your provider's terms for what they do with
prompt content.

### Optional: hosted embedding providers

Semantic search is **off by default**, and its default embedding model
(`sentence-transformers`) runs entirely on your machine — nothing leaves.

If you switch to `openai` or `cohere`, entity descriptions are sent to that
provider to be embedded. The text has this shape:

```
"Anna's Bedroom Presence - device_tracker entity in the Bedroom area"
```

So entity names and area names — frequently including people's names — are
transmitted to a third party, generally outside the EU. Configure it knowingly,
or stay on the local default:

```json
"vector_db": { "embeddings": { "model": "sentence-transformers" } }
```

### Never: telemetry

Hass-MCP itself sends no telemetry, analytics or crash reports. It has no web
interface, sets no cookies, and loads no tracking scripts.

ChromaDB, the default vector store, ships anonymised usage telemetry to a third
party by default. Hass-MCP **disables it explicitly** when constructing the
client, and the VectorDB image also sets `ANONYMIZED_TELEMETRY=False`.

## What is stored, and for how long

| Store | Location | Contains | Retention |
|---|---|---|---|
| Response cache | `.cache/` (file backend), memory, or Redis | Cached entity states | **TTL-bound** — see [caching](caching/configuration.md); default 300s |
| Vector store | `.vectordb/` | Entity descriptions and embeddings | **Kept until deleted** — no TTL |
| Query history | `.vectordb/` | Natural-language queries, selected entities, optional `user_id` | **Kept until deleted** — no automatic purge |
| Logs | stdout | Entity IDs and search queries (see below) | Whatever collects your logs |

Both `.cache/` and `.vectordb/` are excluded from Git and from Docker build
contexts, so they will not be committed or baked into an image by accident.
They are **not encrypted at rest** and are created with your default
permissions — put them on an appropriately protected filesystem, and remember
that a Redis cache backend is unencrypted unless you configure TLS yourself.

### Deleting stored data

Stopping the server and deleting the directories is the blunt, reliable option:

```bash
rm -rf .cache .vectordb
```

For query history specifically, `app.core.vectordb.history.clear_query_history()`
deletes selectively. It is deliberately **not exposed as an MCP tool** — erasing
data should be an operator action, not something a model can invoke:

```python
import asyncio
from datetime import UTC, datetime, timedelta
from app.core.vectordb.history import clear_query_history

# Everything
asyncio.run(clear_query_history())

# One user's history
asyncio.run(clear_query_history(user_id="alice"))

# Anything older than 30 days — run periodically to bound retention
cutoff = datetime.now(UTC) - timedelta(days=30)
asyncio.run(clear_query_history(before_date=cutoff))
```

It returns `{"deleted_count": N, "success": bool}`. There is no scheduled purge,
so if you want history bounded, run the `before_date` form on a schedule.

## Logs contain identifiers

At the default `INFO` level, log lines include **entity IDs and the text of
search queries**:

```
INFO app.server - Getting entity resource: person.anna
INFO app.server - Searching for entities matching: 'is anna home'
```

An entity ID like `person.anna` is personal data, and queries can be revealing
in their own right. This is intentional — it is what makes the server
diagnosable — but it means:

- Treat logs with the same care as the data itself.
- Be deliberate about where they are shipped; a hosted log aggregator is another
  recipient of personal data.
- Raise the level to reduce exposure: `LOG_LEVEL=WARNING` drops the per-request
  lines while keeping errors.

**Secrets are never logged.** Home Assistant tokens and MCP bearer tokens do not
appear in log output: authentication logs record *that* a token was rejected or
forwarded, never its value, and the cache partition key is a truncated digest
rather than the token.

## Limiting what the server can reach

Hass-MCP gives an LLM broad read access by default. Three controls narrow it, all
documented under [Access Policy](configuration.md#access-policy) and
[Exposed Tools](configuration.md#exposed-tools):

**Restrict which entities are visible at all.** The most effective control — it
applies to state, history, logbook, statistics and traces alike:

```json
"policy": {
  "entity_allowlist": ["light.*", "sensor.living_room_*", "climate.*"]
}
```

An empty allowlist allows everything, so this only takes effect once you list
something.

**Refuse writes**, so the server can look but not act:

```json
"policy": { "read_only": true }
```

**Remove whole capabilities.** For example, drop history and behavioural
analysis from the tool surface:

```json
"tools": { "disabled": ["get_logbook", "get_statistics", "get_system_data"] }
```

A practical starting point for a shared or multi-person household: an
`entity_allowlist` covering only the entities you actually want discussed, plus
`read_only` until you need control.

## Multi-user deployments

With `auth.allow_ha_tokens` enabled, each client presents its own Home Assistant
token and Home Assistant applies that user's own permissions. Cache entries are
**partitioned per caller** so one user cannot be served another's data — see
[Caching and per-user tokens](configuration.md#caching-and-per-user-tokens).

Bearer tokens travel in plaintext over HTTP, so put TLS in front of the HTTP
transports and keep `MCP_HOST` bound as narrowly as the deployment allows.

## Quick checklist

- [ ] Decide what the LLM should see, and set `policy.entity_allowlist`
- [ ] Use `policy.read_only` unless you need control
- [ ] Keep `sentence-transformers` (local) unless you accept sending names to a hosted provider
- [ ] Put `.cache/` and `.vectordb/` on a protected filesystem
- [ ] Schedule `clear_query_history(before_date=...)` if you enable semantic search
- [ ] Check where your logs are shipped, and consider `LOG_LEVEL=WARNING`
- [ ] Read your MCP client's model provider terms — that is the main data flow
