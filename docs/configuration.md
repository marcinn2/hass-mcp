# Configuration

This page covers advanced configuration options for Hass-MCP.

## Configuration Layers

Settings come from three layers. The configuration file is the baseline, and
environment variables override it:

```
built-in defaults  ->  configuration file  ->  environment variables
```

So you can commit a `hass-mcp.json` describing your setup and still override any
single value per deployment — for example injecting `HA_TOKEN` as a secret, or
setting `MCP_PORT` in a container — without editing the file.

## Configuration File

A single JSON document configures the whole server. Start from the annotated
[`config/hass-mcp.example.json`](https://github.com/marcinn2/hass-mcp/blob/master/config/hass-mcp.example.json)
in the repository root:

```bash
cp config/hass-mcp.example.json config/hass-mcp.json
$EDITOR config/hass-mcp.json
```

### Location

The file is found from `HASS_MCP_CONFIG_FILE` when set, otherwise by searching,
in order:

| Order | Location |
|-------|----------|
| 1 | `$HASS_MCP_CONFIG_FILE` (explicit path; a path that does not exist is an error) |
| 2 | `./hass-mcp.json` (also `.hass-mcp.json`, `hass-mcp.yaml`, `hass-mcp.yml`) |
| 3 | `./config/hass-mcp.json` (same alternative names) |
| 4 | `$HASS_MCP_CONFIG_DIR/hass-mcp.json` |
| 5 | `~/.hass-mcp/hass-mcp.json` |
| 6 | `/etc/hass-mcp/hass-mcp.json` |

If no file is found, Hass-MCP runs on defaults and environment variables alone.
YAML is accepted at the same paths, but JSON is the documented format.

### Structure

Every section and key is optional; anything omitted falls back to its default.
JSON has no comment syntax, so keys beginning with `$` (such as `$comment`) are
ignored and can be used for notes.

```json
{
  "home_assistant": {
    "url": "http://homeassistant.local:8123",
    "token": "YOUR_LONG_LIVED_ACCESS_TOKEN",
    "ssl_verify": true
  },
  "server": {
    "transport": "stdio",
    "host": "127.0.0.1",
    "port": 8000,
    "log_level": "INFO",
    "stateless_http": false,
    "json_response": false,
    "session_idle_timeout": 1800,
    "max_sessions": 10000
  },
  "auth": {
    "tokens": ["client-token"],
    "allow_ha_tokens": false
  },
  "tools": {
    "enabled": [],
    "disabled": []
  },
  "policy": {
    "read_only": false,
    "entity_allowlist": [],
    "control_denylist": []
  },
  "cache": {
    "enabled": true,
    "backend": "memory",
    "default_ttl": 300,
    "max_size": 1000,
    "endpoints": {
      "entities": { "ttl": 300, "get_state": { "ttl": 60 } },
      "areas": 3600
    }
  },
  "vector_db": {
    "enabled": false,
    "backend": "chroma",
    "embeddings": { "model": "sentence-transformers", "model_name": "all-MiniLM-L6-v2" },
    "search": { "default_limit": 10, "similarity_threshold": 0.7 }
  }
}
```

| Section | Covers | Overriding variables |
|---------|--------|----------------------|
| `home_assistant` | Connection to Home Assistant | `HA_URL`, `HA_TOKEN`, `HA_SSL_VERIFY` |
| `server` | MCP transport, bind address, log level, session behaviour | `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT` (`PORT`), `LOG_LEVEL`, `MCP_STATELESS_HTTP`, `MCP_JSON_RESPONSE`, `MCP_SESSION_IDLE_TIMEOUT`, `MCP_MAX_SESSIONS` |
| `auth` | MCP bearer authentication (HTTP transports) | `MCP_AUTH_TOKENS`, `MCP_ALLOW_HA_TOKENS` |
| `tools` | Which MCP tools are exposed | `MCP_TOOLS_ENABLED`, `MCP_TOOLS_DISABLED` |
| `policy` | Read-only mode, entity allowlist, control denylist | `HASS_MCP_READ_ONLY`, `HASS_MCP_ENTITY_ALLOWLIST`, `HASS_MCP_CONTROL_DENYLIST` |
| `cache` | Response caching and per-endpoint TTLs | `HASS_MCP_CACHE_*` |
| `vector_db` | Semantic entity search | `HASS_MCP_VECTOR_DB_*`, `HASS_MCP_EMBEDDING_*`, `HASS_MCP_SEARCH_*`, `HASS_MCP_INDEXING_*` |

Unknown top-level sections are ignored with a warning, which catches typos such
as `home_asistant`. A malformed file is a startup error rather than a silent
fallback to defaults, so a broken config never sends requests to the wrong
Home Assistant instance.

Per-endpoint cache TTLs have no environment equivalent, so the `cache.endpoints`
map is only configurable through the file. See
[Cache Configuration](caching/configuration.md) for its full schema.
## Sessions and the HTTP Transports

`stdio` has no request layer, so none of this applies — the process *is* the
session. The rest concerns `streamable-http` and `sse`.

By default `streamable-http` is **stateful**: the server issues an
`Mcp-Session-Id` header on `initialize`, the client echoes it on later requests,
and that session's state lives in memory.

```
POST /mcp  (initialize)
  200  Mcp-Session-Id: 45a93e369120421a896d467e7c2bd786
       Content-Type: text/event-stream
```

| Setting | Default | Effect |
|---|---|---|
| `stateless_http` | `false` | `true` disables sessions entirely — no `Mcp-Session-Id`, each request independent |
| `json_response` | `false` | `true` returns `application/json` instead of `text/event-stream` |
| `session_idle_timeout` | `1800` | Seconds a session may idle before it is terminated; its ID then answers `404` and the client must re-initialize |
| `max_sessions` | `10000` | Ceiling on concurrent sessions — a memory bound |

```json
{
  "server": {
    "transport": "streamable-http",
    "stateless_http": false,
    "json_response": false,
    "session_idle_timeout": 1800,
    "max_sessions": 10000
  }
}
```

```bash
export MCP_STATELESS_HTTP=false
export MCP_JSON_RESPONSE=false
export MCP_SESSION_IDLE_TIMEOUT=1800
export MCP_MAX_SESSIONS=10000
```

The server reports which mode it started in:

```
Sessions enabled: idle timeout 1800.0s, max 10000 concurrent
Sessions disabled (stateless_http): each request is independent
```

### When to change them

**`stateless_http` is the one that matters.** Sessions live in this process's
memory, so they pin a client to one instance. Running more than one replica
behind a load balancer requires `stateless_http: true`, or a client's second
request may land on an instance that has never heard of its session. It also
suits serverless or scale-to-zero deployments. For a single self-hosted server,
leave it alone.

Note that scaling out needs more than this setting: the response cache would
also have to move to the Redis backend, since the memory backend is per-process.

**`json_response`** helps with basic clients or proxies that mishandle
Server-Sent Events, at the cost of server-initiated streaming.

**`session_idle_timeout`** trades memory against re-handshakes. Lower it to
reclaim sessions sooner; raise it if clients idle a long time between calls and
you would rather they not re-initialize.

**`max_sessions`** is worth lowering only if the port is reachable from
somewhere you do not fully trust, as a cheap ceiling against session
exhaustion. The default of 10,000 is far beyond what a self-hosted instance
needs.

### Sessions do not carry credentials

Sessions are long-lived, but bearer authentication is evaluated **per request**.
With `auth.allow_ha_tokens` enabled, a client must therefore send its Home
Assistant token on **every** request, not only on `initialize` — the token is
read from the header and discarded when the request completes.

A client that authenticates only during the handshake will find later calls
falling back to the server's configured `HA_TOKEN`, or failing outright when
none is set. This is deliberate: a session is not a standing grant of
credentials.

### Resumability is not enabled

FastMCP supports an `event_store` for resumable sessions, letting a client
reconnect and replay missed events. This project does not configure one, so a
dropped SSE stream means the client must re-initialize rather than resume.

## Access Policy

Three optional controls, **all permissive by default** — an unconfigured server
behaves exactly as it did before this existed:

| Setting | Default | Effect when set |
|---|---|---|
| `read_only` | `false` | `true` refuses every write: service calls and config CRUD. Reads keep working |
| `entity_allowlist` | `[]` | **Empty allows everything.** Once any pattern is present the list is authoritative and unmatched entities are denied, for reads *and* writes |
| `control_denylist` | `[]` | Empty denies nothing. A listed entity can be read but never controlled, even when it passes the allowlist |

Both lists take exact entity IDs or fnmatch globs:

```json
{
  "policy": {
    "read_only": false,
    "entity_allowlist": ["light.living_room", "sensor.gpu_*"],
    "control_denylist": ["lock.*", "cover.garage_*"]
  }
}
```

```bash
export HASS_MCP_READ_ONLY=false
export HASS_MCP_ENTITY_ALLOWLIST="light.living_room,sensor.gpu_*"
export HASS_MCP_CONTROL_DENYLIST="lock.*,cover.garage_*"
```

For long lists, keep patterns in a file — one per line, `#` starts a comment.
Inline and file entries are combined:

```bash
export HASS_MCP_ENTITY_ALLOWLIST_FILE=/etc/hass-mcp/allowlist.txt
export HASS_MCP_CONTROL_DENYLIST_FILE=/etc/hass-mcp/denylist.txt
```

### Where it is enforced

**Read-only mode is enforced on the shared HTTP client**, as a request hook that
refuses write methods. That covers every call site rather than only the ones
routed through `call_service` — the API layer builds request URLs in ~30 places
and several construct service calls directly, so a per-function check left real
gaps. Requests added in future are covered automatically.

`POST`-shaped reads stay allowed: `/api/template` and
`/api/config/core/check_config` change nothing.

**The allowlist and denylist are enforced per entity**, at each point an entity
is named:

| Path | Control |
|---|---|
| `call_service` | denylist + allowlist, per target entity |
| `get_entity_state`, `get_entities`, `system_overview` | allowlist |
| `get_entity_history`, `get_entity_history_range`, `get_entity_logbook` | allowlist |
| `get_entity_statistics`, `get_entity_statistics_range` | allowlist |
| `get_automation_traces`, `get_automation_execution_log` | allowlist |
| `get_script_config` | allowlist |
| `trigger_automation`, `enable_automation`, `disable_automation`, `run_script` | denylist + allowlist |
| `manage_item` write actions | read-only |

A refusal comes back as `{"error": ...}` naming the setting responsible, so the
caller can tell policy apart from a Home Assistant failure.

**One known gap.** `get_automation_config` and `update_automation` address Home
Assistant's numeric automation *config* ID rather than an entity ID, so they
cannot be matched against entity patterns. Both are covered by read-only mode,
but not by the entity lists. Use `tools.disabled` if you need them off
entirely.

### Two caveats

**Policy is fixed for the process lifetime.** Reads are filtered inside cached
functions, so a cached result reflects the policy in force when it was computed.
Changing policy means restarting the server.

**This is a guardrail, not a security boundary.** It constrains what this server
will do with its Home Assistant token; it does not reduce what that token can do.
For a real boundary, issue a scoped Home Assistant token.

### Adopting the stricter posture

This layer is ported from
[paultanger/ha-mcp-server](https://github.com/paultanger/ha-mcp-server), which
defaults to read-only with a *fail-closed* allowlist — an empty list denies
everything. That inversion is deliberate here: configuring nothing must not
break an existing deployment. Their proposed configuration is written out in
full as a `$comment_fork_proposal` block in
[`config/hass-mcp.example.json`](https://github.com/marcinn2/hass-mcp/blob/master/config/hass-mcp.example.json).

Note that with their posture the allowlist becomes mandatory: `read_only: false`
plus an empty allowlist would be permissive here, but in a fail-closed design an
empty allowlist denies every read.

## Exposed Tools

Hass-MCP knows how to expose **114** tools but exposes **35** by default.
Most of the other 79 are the pre-consolidation originals that the unified tools
replaced: they still work, but exposing all of them would fill every client's
tool list — and its token budget — with near-duplicates. A handful are tools
ported from sibling forks that are off for their own reasons — a generic REST
passthrough, and read-only diagnostics for traces, updates and HACS. See the
`$comment_disabled` block in the example config for the full breakdown.

The surface is configurable:

```json
{
  "tools": {
    "enabled": [],
    "disabled": ["restart_ha"]
  }
}
```

| Setting | Meaning |
|---|---|
| `enabled` omitted or `[]` | the default set of 35 tools |
| `enabled: ["all"]` | every one of the 114 tools |
| `enabled: ["get_entity", "list_items"]` | exactly those two |
| `disabled: [...]` | subtracted from whatever `enabled` produced |

`disabled` is applied last, so it trims the defaults without you having to
restate them — `{"disabled": ["restart_ha"]}` gives 34 tools.

Via the environment, both are comma-separated:

```bash
export MCP_TOOLS_ENABLED="get_entity,list_items,get_item"
export MCP_TOOLS_DISABLED="restart_ha"
```

An unknown tool name is logged as a warning and ignored rather than failing
startup, so a typo cannot take the server down. On boot the server reports what
it settled on:

```
Exposing 35 of 114 available tools
```

### Which tools exist

[`config/hass-mcp.example.json`](https://github.com/marcinn2/hass-mcp/blob/master/config/hass-mcp.example.json)
lists the default set explicitly and names every non-default tool in a
`$comment_disabled` block, grouped by module. The authoritative list lives in
[`app/tools/registry.py`](https://github.com/marcinn2/hass-mcp/blob/master/app/tools/registry.py),
where each entry records whether it is on by default.

Two reasons you might narrow the surface: trimming the tool list reduces the
tokens every request spends describing tools, and removing `restart_ha` (or the
`manage_item` write paths) gives a read-mostly deployment.

## MCP Bearer Authentication

The HTTP transports (`streamable-http`, `sse`) **require** bearer
authentication. Every request must carry
`Authorization: Bearer <token>`, and the server refuses to start if no
authentication is configured — better a clear startup error than a port that
rejects everything while looking healthy.

`stdio` is unaffected: it has no request layer, nothing is enforced, and the
configured `HA_TOKEN` is used exactly as before.

### How a token is handled

| Incoming `Authorization` | `allow_ha_tokens` | Result |
|---|---|---|
| absent, or not `Bearer` | any | **401** `missing_token` |
| listed in `auth.tokens` | any | **200** — Home Assistant calls use `home_assistant.token` |
| not listed | `true` | **200** — the token becomes *that request's* Home Assistant token |
| not listed | `false` | **401** `invalid_token` |

The third row is the interesting one: it lets each client bring its own Home
Assistant credential instead of sharing the server's. Home Assistant is then
the authority on whether that token is valid, so an invalid one surfaces as an
HA authentication error rather than a 401 from hass-mcp.

A token listed in `auth.tokens` is never forwarded to Home Assistant, even when
`allow_ha_tokens` is enabled.

### Configuring it

```json
{
  "server": { "transport": "streamable-http", "host": "0.0.0.0", "port": 8000 },
  "auth": {
    "tokens": ["client-one-token", "client-two-token"],
    "allow_ha_tokens": false
  }
}
```

Or via the environment, which overrides the file:

```bash
export MCP_AUTH_TOKENS="client-one-token,client-two-token"
export MCP_ALLOW_HA_TOKENS=false
```

- **`MCP_AUTH_TOKENS`** — comma-separated client tokens (a JSON array in the
  file). Compared in constant time, so a match position cannot be inferred from
  response timing.
- **`MCP_ALLOW_HA_TOKENS`** — when `true`, an unrecognised token is treated as a
  Home Assistant token for that request.

Either one on its own is enough to start: with only `allow_ha_tokens` enabled
the server acts as a pure pass-through and Home Assistant does all the
validating.

### Deployment postures

`tokens` and `allow_ha_tokens` combine into four postures. Pick by asking whose
Home Assistant credential should be used for a request.

| `auth.tokens` | `auth.allow_ha_tokens` | Posture |
|---|---|---|
| set | `false` | **Shared credential.** Clients authenticate with tokens you issue; every Home Assistant call uses the server's `HA_TOKEN`. The usual choice. |
| empty | `true` | **Pass-through.** Every bearer *is* that request's Home Assistant token — Home Assistant does all validation, and each user's own HA permissions apply. |
| set | `true` | **Mixed.** A listed token uses the server's credential; anything else is forwarded to Home Assistant. For trusted service clients alongside per-user ones. |
| empty | `false` | **Refused at startup**, with a message naming both fixes. |

A configured MCP token is never forwarded to Home Assistant, even in the mixed
posture.

!!! warning "Send the token on every request"
    Bearer authentication is evaluated per request, while sessions are
    long-lived. In pass-through mode a client must therefore present its Home
    Assistant token on **every** request, not only on `initialize` — see
    [Sessions do not carry credentials](#sessions-do-not-carry-credentials).

**Shared credential** is the right default. Issue one token per client so they
can be revoked individually, and rotate by adding the new token, moving clients
over, then removing the old one.

**Pass-through** is how you get multi-user behaviour, and the server can hold no
Home Assistant credential at all — leave `HA_TOKEN` unset and every request
brings its own:

```bash
export MCP_ALLOW_HA_TOKENS=true
# HA_TOKEN deliberately unset
```

The trade-off is that a request arriving without a bearer token then has no Home
Assistant access whatsoever, and the server can do nothing on its own behalf
(no background work, no startup checks against Home Assistant).

`stdio` sits outside all of this: no request layer, nothing enforced, and the
configured `HA_TOKEN` used exactly as before.

### Caching and per-user tokens

When `allow_ha_tokens` is enabled, cache entries are partitioned per caller: the
cache key includes a digest of the request's Home Assistant token. Two users
issuing the same query do not share an entry, because Home Assistant answers
each according to that user's permissions — and on a cache hit Home Assistant is
never consulted, so a shared entry would bypass its permission model entirely.

Single-token deployments are unaffected: with no per-request token the key is
unpartitioned exactly as before, so no cache efficiency is lost.

### Security notes

- A rejected request never reaches the MCP server; the 401 carries a
  `WWW-Authenticate: Bearer` header per RFC 6750.
- `MCP_HOST` defaults to `127.0.0.1`. Binding `0.0.0.0` exposes the port, so put
  it behind TLS and network controls — bearer tokens are credentials in
  plaintext over HTTP.
- With `allow_ha_tokens` enabled, anyone who can reach the port can supply a
  Home Assistant token of their own. That is the intended way to support
  multiple users, but it means the port must not be openly reachable.
- Bearer tokens are plaintext credentials on the wire. These transports want TLS
  in front of them, and `MCP_HOST` should stay bound as narrowly as the
  deployment allows.
- Generate tokens with something like `openssl rand -hex 32`, and keep them in
  the environment rather than a committed configuration file.

### Secrets

Prefer environment variables for tokens and API keys, especially when the
configuration file is committed to version control:

```bash
export HA_TOKEN="your-long-lived-token"
```

A full environment-variable template lives at
[`config/hass-mcp.example.env`](https://github.com/marcinn2/hass-mcp/blob/master/config/hass-mcp.example.env),
which lists every variable with its default:

```bash
cp config/hass-mcp.example.env .env
```

## Environment Variables

Every variable below overrides the corresponding configuration file value.


Hass-MCP uses the following environment variables:

### Required Variables

- **`HA_URL`**: Home Assistant URL
  - Format: `http://hostname:port` or `https://hostname:port`
  - Examples:
    - `http://homeassistant.local:8123`
    - `https://ha.example.com:8123`
    - `http://192.168.1.100:8123`

- **`HA_TOKEN`**: Long-lived access token
  - Create in Home Assistant → Profile → Long-lived access tokens
  - Format: Long alphanumeric string

### Optional Variables

- **`HASS_MCP_CONFIG_FILE`**: Explicit path to the configuration file (optional)
  - A path that does not exist is a startup error
- **`HASS_MCP_CONFIG_DIR`**: Additional directory to search for the configuration file
- **`HA_TIMEOUT`**: HTTP request timeout in seconds (default: 30)
- **`LOG_LEVEL`**: Logging level (default: `INFO`)
  - Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`
  - `INFO` includes per-request lines naming entity IDs and search queries,
    which are personal data. `WARNING` drops those and keeps errors — see
    [Privacy & Personal Data](privacy.md#logs-contain-identifiers)
  - Also settable as `server.log_level` in the configuration file

### Transport Variables

- **`MCP_TRANSPORT`**: Transport mode (default: `stdio`)
  - Options: `stdio`, `sse`, `streamable-http`
- **`MCP_HOST`**: Bind address for the HTTP transports (default: `127.0.0.1`)
- **`MCP_PORT`**: Bind port for the HTTP transports (default: `8000`)
- **`PORT`**: Alternative port variable for Smithery compatibility; `MCP_PORT` wins

### Session Variables (HTTP transports)

- **`MCP_STATELESS_HTTP`**: Disable sessions; each request independent (default: `false`)
  - Required to run more than one replica behind a load balancer
- **`MCP_JSON_RESPONSE`**: Return JSON instead of SSE streams (default: `false`)
- **`MCP_SESSION_IDLE_TIMEOUT`**: Seconds before an idle session is terminated (default: `1800`)
- **`MCP_MAX_SESSIONS`**: Maximum concurrent sessions (default: `10000`)

See [Sessions and the HTTP Transports](#sessions-and-the-http-transports).

### Access Policy Variables

- **`HASS_MCP_READ_ONLY`**: Refuse all writes (default: `false`)
- **`HASS_MCP_ENTITY_ALLOWLIST`**: Comma-separated entity IDs or globs; empty
  allows everything
- **`HASS_MCP_CONTROL_DENYLIST`**: Comma-separated entity IDs or globs that can
  be read but never controlled
- **`HASS_MCP_ENTITY_ALLOWLIST_FILE`** / **`HASS_MCP_CONTROL_DENYLIST_FILE`**:
  Newline-delimited pattern files, combined with the inline lists

### Tool Surface Variables

- **`MCP_TOOLS_ENABLED`**: Comma-separated allow-list of tools to expose
  - Empty for the default 35; `all` for every one of the 114
- **`MCP_TOOLS_DISABLED`**: Comma-separated tools to remove from that set

### Authentication Variables

- **`MCP_AUTH_TOKENS`**: Comma-separated bearer tokens that authenticate MCP clients
  - Required for the HTTP transports unless `MCP_ALLOW_HA_TOKENS` is enabled
- **`MCP_ALLOW_HA_TOKENS`**: Treat an unrecognised bearer token as that request's
  Home Assistant token (default: `false`)

### SSL/TLS Configuration

- **`HA_SSL_VERIFY`**: SSL certificate verification mode (default: `true`)
  - `true` - Use system CA certificates (default, secure)
  - `false` - Disable SSL verification (for self-signed certificates)
  - `/path/to/ca.pem` - Use custom CA certificate bundle

  **Security Warning**: Disabling SSL verification (`HA_SSL_VERIFY=false`) makes connections vulnerable to man-in-the-middle attacks. Only use in trusted networks with self-signed certificates. For production, use proper SSL certificates or custom CA bundles.

### Cache Configuration Variables

- **`HASS_MCP_CACHE_ENABLED`**: Enable/disable caching (default: `true`)
  - Options: `true`, `false`, `1`, `0`, `yes`, `no`
- **`HASS_MCP_CACHE_BACKEND`**: Cache backend type (default: `memory`)
  - Options: `memory`, `redis`, `file`
- **`HASS_MCP_CACHE_DEFAULT_TTL`**: Default cache TTL in seconds (default: `300`)
- **`HASS_MCP_CACHE_MAX_SIZE`**: Maximum cache size (default: `1000`)
- **`HASS_MCP_CACHE_REDIS_URL`**: Redis URL for Redis backend (optional)
  - Example: `redis://localhost:6379/0`
- **`HASS_MCP_CACHE_DIR`**: Cache directory for file backend (default: `.cache`)
- **`HASS_MCP_CACHE_CONFIG_FILE`**: Path to cache configuration file (optional)
  - Supports JSON and YAML formats
  - Example: `/path/to/cache_config.json`

## Configuration Examples

### Basic Configuration

```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "HA_URL", "-e", "HA_TOKEN", "ghcr.io/marcinn2/hass-mcp"],
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "your_token_here"
      }
    }
  }
}
```

### With Custom Timeout

```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "HA_URL", "-e", "HA_TOKEN", "-e", "HA_TIMEOUT", "ghcr.io/marcinn2/hass-mcp"],
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "your_token_here",
        "HA_TIMEOUT": "60"
      }
    }
  }
}
```

### With Debug Logging

```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "HA_URL", "-e", "HA_TOKEN", "-e", "LOG_LEVEL", "ghcr.io/marcinn2/hass-mcp"],
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "your_token_here",
        "LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

### With Self-Signed Certificates

For Home Assistant instances using self-signed SSL certificates:

```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "HA_URL", "-e", "HA_TOKEN", "-e", "HA_SSL_VERIFY", "ghcr.io/marcinn2/hass-mcp"],
      "env": {
        "HA_URL": "https://homeassistant.local:8123",
        "HA_TOKEN": "your_token_here",
        "HA_SSL_VERIFY": "false"
      }
    }
  }
}
```

**Security Warning**: Only disable SSL verification in trusted networks. This makes connections vulnerable to man-in-the-middle attacks.

### With Custom CA Certificate

For Home Assistant instances using a custom CA certificate:

```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-v", "/path/to/certs:/certs:ro",
        "-e", "HA_URL",
        "-e", "HA_TOKEN",
        "-e", "HA_SSL_VERIFY",
        "ghcr.io/marcinn2/hass-mcp"
      ],
      "env": {
        "HA_URL": "https://homeassistant.local:8123",
        "HA_TOKEN": "your_token_here",
        "HA_SSL_VERIFY": "/certs/ca.pem"
      }
    }
  }
}
```

**Note**: The volume mount (`-v /path/to/certs:/certs:ro`) is required to make the CA certificate accessible inside the Docker container. Replace `/path/to/certs` with the actual path to your certificate directory.

### Docker with Network Configuration

For Docker Desktop on Mac/Windows:

```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "HA_URL",
        "-e", "HA_TOKEN",
        "--network", "host",
        "ghcr.io/marcinn2/hass-mcp"
      ],
      "env": {
        "HA_URL": "http://localhost:8123",
        "HA_TOKEN": "your_token_here"
      }
    }
  }
}
```

## Network Configuration

### Local Network Setup

If Home Assistant is on the same local network:

- Use the Home Assistant hostname: `http://homeassistant.local:8123`
- Or use the IP address: `http://192.168.1.100:8123`

### Docker Desktop (Mac/Windows)

When running Docker Desktop:

- Use `http://host.docker.internal:8123` to access host services
- Or use the actual IP address of your host machine

### Remote Access

For remote Home Assistant instances:

- Use HTTPS: `https://ha.example.com:8123`
- Ensure proper SSL certificates
- May require firewall/port forwarding configuration

## Security Considerations

1. **Token Security**
   - Never commit tokens to version control
   - Use environment variables or secure configuration management
   - Rotate tokens regularly

2. **Network Security**
   - Use HTTPS for remote access
   - Use strong passwords and tokens
   - Consider VPN for remote access

3. **Permissions**
   - Grant only necessary permissions when creating tokens
   - Review token permissions regularly

## Cache Configuration

The caching system reduces API calls to Home Assistant by caching relatively static data. Configuration can be managed via environment variables or a configuration file.

### Environment Variables

All cache configuration can be set via environment variables (highest priority):

```bash
export HASS_MCP_CACHE_ENABLED=true
export HASS_MCP_CACHE_BACKEND=memory
export HASS_MCP_CACHE_DEFAULT_TTL=300
export HASS_MCP_CACHE_MAX_SIZE=1000
```

### Configuration File

You can also use a configuration file (JSON or YAML) for more complex setups:

**JSON Example** (`cache_config.json`):
```json
{
  "enabled": true,
  "backend": "memory",
  "default_ttl": 300,
  "max_size": 1000,
  "endpoints": {
    "entities": {"ttl": 1800},
    "automations": {"ttl": 3600},
    "areas": {"ttl": 3600}
  }
}
```

**YAML Example** (`cache_config.yaml`):
```yaml
enabled: true
backend: memory
default_ttl: 300
max_size: 1000
endpoints:
  entities:
    ttl: 1800
  automations:
    ttl: 3600
  areas:
    ttl: 3600
```

**Simple Format** (integer TTL):
```json
{
  "endpoints": {
    "entities": 1800,
    "automations": 3600
  }
}
```

### Per-Endpoint TTL Configuration

You can configure different TTL values for different endpoints:

```json
{
  "endpoints": {
    "entities": {"ttl": 60},           // 1 minute for entity states
    "entities.list": {"ttl": 1800},    // 30 minutes for entity lists
    "automations": {"ttl": 3600},      // 1 hour for automations
    "areas": {"ttl": 3600}             // 1 hour for areas
  }
}
```

### Configuration Priority

Configuration is loaded in the following order (highest to lowest priority):

1. **Environment Variables** - Highest priority
2. **Configuration File** - Medium priority
3. **Default Values** - Lowest priority

### Runtime Configuration Management

You can manage cache configuration at runtime using the API:

- **Get configuration**: `get_cache_configuration()`
- **Update endpoint TTL**: `update_cache_endpoint_ttl(domain, ttl, operation)`
- **Reload configuration**: `reload_cache_config()`

### Cache Backends

The cache system supports multiple backends:

- **`memory`**: In-memory cache (default, fastest, no persistence)
- **`redis`**: Redis backend (distributed, persistent, requires Redis)
- **`file`**: File-based cache (persistent, slower, no external dependencies)

### Recommended TTL Values

Based on data volatility:

- **Very Long TTL (1 hour)**: Areas, zones, blueprints, system config, HA version
- **Long TTL (30 minutes)**: Entities metadata, automations, scripts, scenes, devices, helpers, tags
- **Medium TTL (5 minutes)**: Integrations, device statistics, domain summaries
- **Short TTL (1 minute)**: Entity states, entity lists with state info
- **No Caching**: Logbook, history, statistics, events, templates, notifications

## Troubleshooting Configuration

### Invalid URL Format

**Error**: "Invalid URL format"

**Solution**: Ensure URL includes protocol (`http://` or `https://`) and port

### Connection Timeout

**Error**: "Connection timeout"

**Solution**:
- Increase `HA_TIMEOUT` value
- Check network connectivity
- Verify Home Assistant is accessible

### Authentication Failed

**Error**: "401 Unauthorized"

**Solution**:
- Verify `HA_TOKEN` is correct
- Check token hasn't been revoked
- Create a new token if needed

### Cache Configuration Issues

**Error**: "Cache config file not found"

**Solution**:
- Verify the path in `HASS_MCP_CACHE_CONFIG_FILE` is correct
- Check file permissions
- Use absolute paths for clarity

**Error**: "Invalid cache configuration"

**Solution**:
- Verify JSON/YAML syntax is correct
- Check that TTL values are positive integers
- Ensure backend type is one of: `memory`, `redis`, `file`
