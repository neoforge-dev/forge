# Permission Pre-loader for FORGE Fleet

**HRN-019: Permission pre-loader implementation**

Configure all agents with required permissions at fleet startup to avoid interactive prompts during autonomous operation.

## Overview

The Permission Pre-loader provides a robust system for managing agent permissions across the FORGE fleet. It enables:

- **Pre-configured permissions** - Load permission profiles from JSON config
- **Per-agent permissions** - Different permission sets for different agent types
- **Domain-specific permissions** - Additional permissions based on domain (production, development, testing)
- **Critical permission validation** - Fail fast if critical permissions are missing
- **Auto-detection** - Automatically detect agent type from agent ID
- **Verification history** - Track permission verification over time

## Quick Start

```python
from forge_harness.fleet.permissions import create_permission_preloader

# Create pre-loader (uses defaults if no config file)
preloader = create_permission_preloader()

# Apply permissions to an agent
preloader.apply_permissions("backend-engineer", domain="production")

# Verify permissions
verified = preloader.verify_permissions("forge:tech-001", "backend-engineer")
if not verified:
    raise RuntimeError("Critical permissions missing")
```

## Agent Types

The system supports the following agent types out of the box:

| Agent Type | Description | Critical Permissions |
|------------|-------------|---------------------|
| `orchestrator` | Orchestrates tasks across agents | `file:read`, `task:create` |
| `backend-engineer` | Backend development | `file:read`, `file:write`, `test:run` |
| `frontend-builder` | Frontend development | `file:read`, `file:write`, `test:run` |
| `debug-detective` | Debugging and diagnostics | `file:read`, `test:run` |
| `qa-specialist` | Testing and quality | `file:read`, `test:run` |
| `content-creator` | Content generation | `file:read`, `file:write` |

## Permission Categories

Permissions are organized into categories:

### File Operations
- `file:read` - Read files
- `file:write` - Write/edit files
- `file:create` - Create new files
- `file:delete` - Delete files

### Git Operations
- `git:status` - View git status
- `git:log` - View git history
- `git:diff` - View git diffs
- `git:commit` - Create commits
- `git:push` - Push to remote

### Testing
- `test:run` - Run tests
- `test:debug` - Debug failing tests
- `test:coverage` - Run coverage reports
- `test:profile` - Profile test performance

### Database
- `db:migrate` - Run database migrations
- `db:reset` - Reset database (development only)
- `db:migrate:prod` - Production migrations

### Deployment
- `deployment:stage` - Deploy to staging
- `deployment:prod` - Deploy to production
- `deployment:cdn` - Deploy to CDN
- `deployment:vercel` - Deploy to Vercel

### Command Execution
- `command:exec` - Execute shell commands

### Task Management
- `task:create` - Create tasks for other agents
- `tmux:send` - Send commands to tmux sessions

### Logging
- `log:read` - Read log files
- `log:prod:read` - Read production logs

### API Access
- `api:read` - Read from APIs
- `api:write` - Write to APIs

### CMS
- `cms:publish` - Publish content to CMS

## Configuration

### Using Default Permissions

The pre-loader includes sensible defaults for all agent types:

```python
preloader = create_permission_preloader()
# Uses DEFAULT_PERMISSIONS defined in the module
```

### Using Custom Config

Create a `config/agent_permissions.json` file:

```json
{
  "agents": {
    "backend-engineer": {
      "permissions": [
        "file:read",
        "file:write",
        "test:run"
      ],
      "domain_permissions": {
        "production": ["deployment:prod"],
        "testing": ["test:debug"]
      },
      "critical_permissions": ["file:read", "test:run"]
    }
  }
}
```

Load it:

```python
from pathlib import Path

preloader = create_permission_preloader(
    config_path=Path("config/agent_permissions.json")
)
```

## Domain-Specific Permissions

Add extra permissions based on the domain:

```python
# Apply base permissions + production domain permissions
preloader.apply_permissions("backend-engineer", domain="production")

# Get all permissions including domain-specific ones
permissions = preloader.get_permissions("backend-engineer", domain="production")
print(permissions)  # Includes deployment:prod, db:migrate:prod, etc.
```

### Built-in Domains

- **production** - Production deployments and migrations
- **development** - Developer tools and resets
- **testing** - Test debugging and profiling

## Verification

### Basic Verification

```python
# Verify an agent has all required permissions
verified = preloader.verify_permissions("forge:tech-001", "backend-engineer")

if not verified:
    print("Missing critical permissions!")
```

### Auto-Detection

The system can auto-detect agent type from the agent ID:

```python
# Auto-detects "backend-engineer" from "tech" in ID
preloader.verify_permissions("forge:tech-001")

# Detection patterns:
# - "tech" -> backend-engineer
# - "frontend", "ui" -> frontend-builder
# - "debug" -> debug-detective
# - "qa", "test" -> qa-specialist
# - "content" -> content-creator
# - "orchestrat" -> orchestrator
```

### Fail-Fast Mode

Configure the pre-loader to raise an error on missing critical permissions:

```python
preloader = PermissionPreloader(fail_on_missing_critical=True)
preloader.load_permissions()
preloader.apply_permissions("backend-engineer")

# This will raise RuntimeError if critical permissions missing
preloader.verify_permissions("agent-1", "backend-engineer")
```

### Verification History

Track verification results over time:

```python
# Verify multiple agents
preloader.verify_permissions("agent-1", "backend-engineer")
preloader.verify_permissions("agent-2", "frontend-builder")

# Get all verification history
history = preloader.get_verification_history()

# Filter by agent ID
agent_history = preloader.get_verification_history(agent_id="agent-1")

# Filter by agent type
type_history = preloader.get_verification_history(agent_type="backend-engineer")

# Inspect results
for result in history:
    print(f"{result.agent_id}: {result.verified}")
    if result.critical_missing:
        print(f"  Missing critical: {result.critical_missing}")
```

## Dynamic Permission Management

### Add Permissions

```python
# Add a regular permission
preloader.add_permission("backend-engineer", "custom:permission")

# Add a critical permission
preloader.add_permission("backend-engineer", "security:scan", critical=True)

# Re-apply permissions
preloader.apply_permissions("backend-engineer")
```

### Remove Permissions

```python
# Remove a permission
preloader.remove_permission("backend-engineer", "git:push")

# Re-apply permissions
preloader.apply_permissions("backend-engineer")
```

### Reset to Defaults

```python
# Reset an agent's permissions to defaults
preloader.reset_agent_permissions("backend-engineer")
```

## Export Configuration

Export the current permission profiles to a JSON file:

```python
from pathlib import Path

preloader.export_config(Path("config/exported_permissions.json"))
```

This is useful for:
- Backing up current configuration
- Sharing configuration across environments
- Creating templates for new projects

## Integration with Fleet

### Fleet Startup

Apply permissions to all agents at fleet startup:

```python
from forge_harness.fleet.permissions import create_permission_preloader

def initialize_fleet():
    preloader = create_permission_preloader()

    # Apply permissions for all agent types
    for agent_type in ["backend-engineer", "frontend-builder", "qa-specialist"]:
        preloader.apply_permissions(agent_type, domain="production")

    return preloader
```

### Agent Creation

Verify permissions when creating new agents:

```python
def create_agent(agent_id: str, agent_type: str):
    preloader = get_permission_preloader()

    # Verify before creating agent
    if not preloader.verify_permissions(agent_id, agent_type):
        raise RuntimeError(f"Cannot create agent {agent_id}: missing permissions")

    # Create agent...
```

### Health Checks

Include permission status in fleet health checks:

```python
def check_fleet_health():
    preloader = get_permission_preloader()

    for agent in get_all_agents():
        verified = preloader.verify_permissions(agent.id, agent.type)
        if not verified:
            log.warning(f"Agent {agent.id} has missing permissions")
```

## CLI Integration

Add CLI commands for permission management:

```bash
# Verify fleet permissions
forge-harness fleet verify-permissions

# Export current config
forge-harness fleet export-permissions config/permissions.json

# Check permission status for an agent
forge-harness fleet check-permissions forge:tech-001

# Apply permissions to an agent type
forge-harness fleet apply-permissions backend-engineer --domain production
```

## Testing

Run the comprehensive test suite:

```bash
# Run all permission tests
uv run pytest tests/test_fleet/test_permissions.py -v

# Run with coverage
uv run pytest tests/test_fleet/test_permissions.py --cov=forge_harness.fleet.permissions

# Run specific test
uv run pytest tests/test_fleet/test_permissions.py::TestPermissionPreloader::test_verify_permissions_success -v
```

Test coverage: **98%** (41 tests)

## Best Practices

### 1. Load Early
Load and apply permissions during fleet initialization, before creating any agents.

### 2. Use Config Files
Store production permissions in version-controlled config files for consistency.

### 3. Verify Critical Permissions
Always verify critical permissions with `fail_on_missing_critical=True` in production.

### 4. Domain-Specific Permissions
Use domain-specific permissions to enforce environment separation.

### 5. Audit History
Regularly review verification history to catch permission drift.

### 6. Auto-Detection
Leverage auto-detection for consistent agent naming patterns.

### 7. Export Regularly
Export current configurations for backup and documentation.

## Architecture

```
PermissionPreloader
├── load_permissions()          # Load from config or defaults
├── apply_permissions()         # Apply to agent type
├── verify_permissions()        # Verify agent permissions
├── get_permissions()           # Get permission list
├── add_permission()            # Add single permission
├── remove_permission()         # Remove single permission
├── reset_agent_permissions()  # Reset to defaults
├── export_config()            # Export to JSON
└── get_verification_history() # Get verification results

PermissionProfile              # Per-agent permission profile
├── agent_type: str
├── permissions: Set[str]
├── domain_permissions: Dict[str, Set[str]]
├── critical_permissions: Set[str]
└── verified: bool

PermissionVerificationResult   # Verification result record
├── agent_id: str
├── agent_type: str
├── verified: bool
├── missing_permissions: List[str]
├── critical_missing: List[str]
└── timestamp: datetime
```

## Example: Complete Fleet Initialization

```python
from forge_harness.fleet.permissions import create_permission_preloader
from pathlib import Path

def initialize_forge_fleet():
    """Initialize FORGE fleet with permissions."""

    # Load custom config
    config_path = Path("config/agent_permissions.json")
    preloader = create_permission_preloader(
        config_path=config_path if config_path.exists() else None,
        fail_on_missing_critical=True
    )

    # Apply permissions for all agent types
    agent_types = [
        ("orchestrator", None),
        ("backend-engineer", "production"),
        ("frontend-builder", "production"),
        ("debug-detective", "development"),
        ("qa-specialist", "testing"),
        ("content-creator", None),
    ]

    for agent_type, domain in agent_types:
        preloader.apply_permissions(agent_type, domain=domain)
        print(f"Applied permissions for {agent_type}" +
              (f" (domain: {domain})" if domain else ""))

    # Verify all agent types have critical permissions
    for agent_type, _ in agent_types:
        test_agent_id = f"test-{agent_type}-001"
        verified = preloader.verify_permissions(test_agent_id, agent_type)
        if not verified:
            raise RuntimeError(f"Failed to verify {agent_type} permissions")

    print(f"✅ All {len(agent_types)} agent types verified")

    # Export current config for backup
    preloader.export_config(Path(".forge_permissions_backup.json"))

    return preloader

if __name__ == "__main__":
    preloader = initialize_forge_fleet()
```

## Related Modules

- `forge_harness.fleet.dashboard` - Fleet monitoring and health
- `forge_harness.fleet.context_rotation` - Context management
- `forge_harness.fleet.metrics` - Fleet metrics and reporting

## Support

For issues or questions:
- Review test suite: `tests/test_fleet/test_permissions.py`
- Check example config: `config/agent_permissions.json.example`
- See fleet documentation: `forge_harness/fleet/README.md`
