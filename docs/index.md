# Hass-MCP User Documentation

Welcome to the **Hass-MCP** (Home Assistant Model Context Protocol) server documentation!

Hass-MCP enables AI assistants like Claude to interact directly with your Home Assistant instance, providing a comprehensive set of tools for managing and controlling your smart home.

!!! info "About this fork"
    This is [marcinn2/hass-mcp](https://github.com/marcinn2/hass-mcp), a fork of
    [mmornati/hass-mcp](https://github.com/mmornati/hass-mcp), which is itself a
    fork of the original [voska/hass-mcp](https://github.com/voska/hass-mcp) by
    Matt Voska.

    ```
    voska/hass-mcp          original implementation
      └── mmornati/hass-mcp     modular rewrite, caching, VectorDB, docs, CI
            └── marcinn2/hass-mcp   this fork
    ```

    Most of what distinguishes this project from the original upstream comes
    from the parent fork. Added here: structured configuration, MCP bearer
    authentication, a configurable tool surface, the access policy layer, URL
    hardening, and features ported from several sibling forks. See the
    [README](https://github.com/marcinn2/hass-mcp#about-this-fork) for the full
    attribution, and [Privacy & Personal Data](privacy.md) before pointing it at
    a live instance.

    It is an independent personal project — not affiliated with or endorsed by
    Home Assistant, Nabu Casa, or the authors of the upstream projects.

## What is Hass-MCP?

Hass-MCP is a Model Context Protocol (MCP) server that bridges the gap between AI assistants and Home Assistant. It provides **15 unified tools + specialized tools** (consolidated from 92 original tools) organized into **20+ categories** that allow AI assistants to:

- **Query** the state of devices, sensors, and entities
- **Control** lights, switches, climate, and other home automation devices
- **Manage** automations, scripts, scenes, and areas
- **Monitor** system health, logs, and diagnostics
- **Create** and configure new automations and helpers
- **Troubleshoot** issues and analyze usage patterns

## Quick Start

### Prerequisites

1. **Home Assistant** instance running (local or remote)
2. **Long-Lived Access Token** from Home Assistant
   - Go to Home Assistant → Profile → Long-lived access tokens
   - Create a new token with appropriate permissions
3. **Claude Desktop** or another MCP-compatible client

### Configuration

Add Hass-MCP to your Claude Desktop configuration:

**Docker (Recommended):**
```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "HA_URL",
        "-e", "HA_TOKEN",
        "ghcr.io/marcinn2/hass-mcp:latest"
      ],
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "YOUR_LONG_LIVED_TOKEN"
      }
    }
  }
}
```

**Python/uv:**
```json
{
  "mcpServers": {
    "hass-mcp": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/marcinn2/hass-mcp", "hass-mcp"],
      "env": {
        "HA_URL": "http://homeassistant.local:8123",
        "HA_TOKEN": "YOUR_LONG_LIVED_TOKEN"
      }
    }
  }
}
```

!!! info "Privacy"
    Hass-MCP reads your Home Assistant data and passes it to an LLM. See
    [Privacy & Personal Data](privacy.md) for what it touches, where that goes,
    and how to restrict it.

### Verify Connection

Once configured, you can verify the connection by asking Claude:

> "List all the lights in my Home Assistant instance"

If the MCP server is working correctly, Claude will use the `list_entities` tool to retrieve and display your lights.

## Documentation Structure

This documentation is organized by tool categories:

- [**Entities**](entities.md) - Query and control devices, sensors, and other entities
- [**Automations**](automations.md) - Create, manage, and troubleshoot automations
- [**Devices & Areas**](devices-areas.md) - Manage devices and areas in your smart home
- [**Scenes & Scripts**](scenes-scripts.md) - Control scenes and execute scripts
- [**System Management**](system.md) - Monitor system health, logs, and configuration
- [**Services**](services.md) - Call any Home Assistant service
- [**Statistics & Diagnostics**](statistics-diagnostics.md) - Analyze usage patterns and troubleshoot issues
- [**Integrations**](integrations.md) - Manage Home Assistant integrations
- [**Helpers**](helpers.md) - Manage input helpers (booleans, numbers, text, etc.)
- [**Calendars**](calendars.md) - Manage calendar entities and events
- [**Notifications**](notifications.md) - Send and test notifications
- [**Webhooks**](webhooks.md) - Manage and test webhooks
- [**Tags**](tags.md) - Manage RFID/NFC tags
- [**Backups**](backups.md) - Create and manage Home Assistant backups
- [**Blueprints**](blueprints.md) - Import and use automation blueprints
- [**Zones**](zones.md) - Manage zones for location tracking
- [**Events**](events.md) - Fire and manage custom events
- [**Logbook**](logbook.md) - Query the Home Assistant logbook
- [**Templates**](templates.md) - Test Jinja2 templates
- [**Prompts**](prompts.md) - Guided conversations for common tasks

## Best Practices

### Token Efficiency

Hass-MCP is designed to be token-efficient:

- Use `lean=True` format when possible (returns essential fields only)
- Use `domain` filters to narrow down entity searches
- Use `search_query` for targeted searches instead of listing all entities

### Error Handling

All tools include comprehensive error handling:

- Invalid entity IDs return clear error messages
- Connection issues are reported with actionable guidance
- Missing permissions are identified and explained

### Security

- **Never share your `HA_TOKEN`** - it provides full access to your Home Assistant instance
- Use environment variables or secure configuration management
- Regularly rotate your access tokens
- Grant only necessary permissions when creating tokens

## Examples

### Basic Entity Control

```
User: "Turn on the living room lights"
Claude: [Uses entity_action tool]
✅ Lights turned on successfully
```

### Automation Management

```
User: "Show me all my automations and their status"
Claude: [Uses list_automations tool]
📋 Here are your automations:
- Morning Routine: Enabled
- Night Mode: Disabled
...
```

### System Overview

```
User: "Give me an overview of my Home Assistant system"
Claude: [Uses system_overview tool]
🏠 System Overview:
- Total Entities: 156
- Domains: light (23), switch (15), sensor (45)...
- Home Assistant Version: 2025.3.0
```

## Getting Help

- **Issues**: [GitHub Issues](https://github.com/marcinn2/hass-mcp/issues)
- **Documentation**: This site
- **Home Assistant API**: [Official Documentation](https://www.home-assistant.io/integrations/api/)

## License

This project is licensed under the MIT License.
