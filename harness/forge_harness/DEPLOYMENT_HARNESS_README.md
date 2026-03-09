# DeploymentHarness - Railway and Cloudflare Pages Integration

Production-ready deployment automation for FORGE portfolio projects, supporting Railway backend services and Cloudflare Pages static sites.

## Features

- **Railway GraphQL API** - Deploy backend services with environment variable management
- **Cloudflare Pages REST API** - Deploy static frontends with automatic project creation
- **Status Tracking** - Poll deployment progress until completion
- **Rollback Support** - Revert to previous deployment on failures
- **Dry-Run Mode** - Test deployment flows without executing
- **Type-Safe Results** - Comprehensive dataclasses for all operations
- **Async/Await** - Non-blocking I/O for efficient operations

## Installation

The DeploymentHarness is part of the `forge-harness` package:

```bash
cd harness
uv sync
```

## Configuration

Set the following environment variables:

```bash
# Railway
export RAILWAY_TOKEN="your_railway_api_token"

# Cloudflare Pages
export CLOUDFLARE_API_TOKEN="your_cloudflare_api_token"
export CLOUDFLARE_ACCOUNT_ID="your_cloudflare_account_id"
```

Or pass credentials directly when creating the harness:

```python
from forge_harness.deployment_harness import create_deployment_harness

harness = create_deployment_harness(
    railway_token="...",
    cloudflare_token="...",
    cloudflare_account_id="...",
)
```

## Quick Start

### Deploy Backend to Railway

```python
from forge_harness.deployment_harness import create_deployment_harness

# Create harness (uses environment variables by default)
harness = create_deployment_harness()

# Deploy backend service
result = await harness.deploy_to_railway(
    project="interview-simulator",
    service="api",
    project_id="railway_project_id",
    service_id="railway_service_id",
    env_vars={
        "DATABASE_URL": "postgres://...",
        "REDIS_URL": "redis://...",
        "API_KEY": "secret",
    },
)

print(f"Deployment ID: {result.deployment_id}")
print(f"Status: {result.status}")
print(f"URL: {result.url}")

# Wait for completion
final_result = await harness.wait_for_completion(
    result.deployment_id,
    timeout_seconds=600,  # 10 minutes
    poll_interval_seconds=5,
)

if final_result.status == DeploymentStatus.SUCCESS:
    print(f"Deployment succeeded: {final_result.url}")
else:
    print(f"Deployment failed: {final_result.error_message}")
```

### Deploy Frontend to Cloudflare Pages

```python
from pathlib import Path

# Deploy static site
result = await harness.deploy_to_cloudflare(
    project="interview-simulator",
    directory=Path("frontend/dist"),
)

print(f"Deployment ID: {result.deployment_id}")
print(f"Status: {result.status}")
print(f"URL: {result.url}")
print(f"Build logs: {result.build_logs_url}")
```

### Check Deployment Status

```python
# Get current status
status = await harness.get_deployment_status(deployment_id)

print(f"Status: {status.status}")
print(f"Started: {status.started_at}")
print(f"Completed: {status.completed_at}")
```

### Rollback Deployment

```python
# Rollback to previous deployment
success = await harness.rollback(deployment_id)

if success:
    print("Rollback succeeded")
else:
    print("Rollback failed")
```

## API Reference

### DeploymentHarness

Main class for deployment operations.

#### Methods

##### `deploy_to_railway(project, service, env_vars=None, project_id=None, service_id=None)`

Deploy a backend service to Railway.

**Parameters:**
- `project` (str): Project name for logging/tracking
- `service` (str): Service name for logging/tracking
- `env_vars` (dict, optional): Environment variables to set
- `project_id` (str, required): Railway project ID
- `service_id` (str, required): Railway service ID

**Returns:** `DeploymentResult`

**Raises:**
- `ValueError`: If Railway client not configured or IDs missing

##### `deploy_to_cloudflare(project, directory)`

Deploy static site to Cloudflare Pages.

**Parameters:**
- `project` (str): Cloudflare Pages project name
- `directory` (str | Path): Path to built static files

**Returns:** `DeploymentResult`

**Raises:**
- `ValueError`: If Cloudflare client not configured
- `FileNotFoundError`: If directory doesn't exist

##### `get_deployment_status(deployment_id)`

Check deployment status.

**Parameters:**
- `deployment_id` (str): Deployment ID from deploy_to_railway or deploy_to_cloudflare

**Returns:** `DeploymentResult` with current status

**Raises:**
- `ValueError`: If deployment_id not found

##### `rollback(deployment_id)`

Rollback to previous deployment.

**Parameters:**
- `deployment_id` (str): Deployment ID to rollback from

**Returns:** `bool` - True if rollback succeeded

**Raises:**
- `ValueError`: If deployment_id not found

##### `wait_for_completion(deployment_id, timeout_seconds=600, poll_interval_seconds=5)`

Wait for deployment to complete.

**Parameters:**
- `deployment_id` (str): Deployment ID to monitor
- `timeout_seconds` (int): Maximum time to wait (default: 10 minutes)
- `poll_interval_seconds` (int): Polling frequency (default: 5 seconds)

**Returns:** `DeploymentResult` - Final deployment state

**Raises:**
- `TimeoutError`: If deployment doesn't complete within timeout

### DeploymentResult

Dataclass representing deployment operation results.

**Attributes:**
- `deployment_id` (str): Unique deployment identifier
- `platform` (DeploymentPlatform): Platform where deployed (Railway/Cloudflare)
- `status` (DeploymentStatus): Current deployment status
- `url` (str | None): Public URL of deployed service
- `build_logs_url` (str | None): URL to view build logs
- `started_at` (datetime | None): When deployment started
- `completed_at` (datetime | None): When deployment completed
- `error_message` (str | None): Error details if failed
- `metadata` (dict | None): Additional platform-specific data

### DeploymentStatus

Enum of possible deployment states:

- `PENDING` - Deployment queued
- `BUILDING` - Building application
- `DEPLOYING` - Deploying to platform
- `SUCCESS` - Deployment succeeded
- `FAILED` - Deployment failed
- `CANCELLED` - Deployment cancelled
- `ROLLED_BACK` - Deployment rolled back

### DeploymentPlatform

Enum of deployment platforms:

- `RAILWAY` - Railway backend services
- `CLOUDFLARE` - Cloudflare Pages static sites
- `UNKNOWN` - Unknown platform (dry-run mode)

## Dry-Run Mode

Test deployment flows without executing actual deployments:

```python
harness = create_deployment_harness(dry_run=True)

# This will log but not execute
result = await harness.deploy_to_railway(
    project="test",
    service="api",
    project_id="proj-123",
    service_id="svc-456",
)

# Returns successful dry-run result
assert result.deployment_id.startswith("dry-run-railway-")
assert result.status == DeploymentStatus.SUCCESS
```

## Error Handling

The harness provides comprehensive error handling:

```python
from forge_harness.deployment_harness import DeploymentStatus

try:
    result = await harness.deploy_to_railway(
        project="my-app",
        service="api",
        project_id="proj-123",
        service_id="svc-456",
    )

    if result.status == DeploymentStatus.FAILED:
        print(f"Deployment failed: {result.error_message}")
        # Trigger rollback or alert

except ValueError as e:
    # Missing credentials or configuration
    print(f"Configuration error: {e}")

except aiohttp.ClientError as e:
    # Network or API error
    print(f"API error: {e}")

except Exception as e:
    # Unexpected error
    print(f"Unexpected error: {e}")
```

## Integration with HarnessRegistry

The DeploymentHarness is automatically registered in the HarnessRegistry:

```python
from forge_harness.harness_registry import create_harness_registry

# Create registry
registry = create_harness_registry(
    domain="codeswiftr-com",
    project="interview-simulator",
)

# Get deployment harness
deployment = registry.get("deployment")

# Use it
result = await deployment.deploy_to_railway(...)
```

## Railway API Details

The Railway client uses the GraphQL API v2:

**Endpoint:** `https://backboard.railway.app/graphql/v2`

**Authentication:** Bearer token in `Authorization` header

**Key Operations:**
- `serviceDeploy` - Trigger deployment
- `variableUpsert` - Set environment variables
- `deployment` - Query deployment status
- `deploymentRollback` - Rollback deployment

**Documentation:** https://docs.railway.app/reference/public-api

## Cloudflare Pages API Details

The Cloudflare client uses the REST API:

**Endpoint:** `https://api.cloudflare.com/client/v4`

**Authentication:** Bearer token in `Authorization` header

**Key Operations:**
- `POST /accounts/{id}/pages/projects/{name}/deployments` - Create deployment
- `GET /accounts/{id}/pages/projects/{name}/deployments/{id}` - Get status
- `POST /accounts/{id}/pages/projects/{name}/deployments/{id}/retry` - Rollback

**Documentation:** https://developers.cloudflare.com/pages/platform/api/

## Testing

Run the test suite:

```bash
cd harness
uv run pytest tests/test_deployment_harness.py -v
```

Test coverage includes:

- Railway client operations (deploy, status, rollback)
- Cloudflare client operations (deploy, status, rollback)
- DeploymentHarness unified interface
- Status polling and completion waiting
- Error handling and edge cases
- Dry-run mode
- Factory functions

## Examples

### Full Deployment Pipeline

```python
async def deploy_full_stack(domain: str, project: str):
    """Deploy both backend and frontend."""
    harness = create_deployment_harness()

    # 1. Deploy backend to Railway
    backend_result = await harness.deploy_to_railway(
        project=project,
        service="api",
        project_id=get_railway_project_id(domain, project),
        service_id=get_railway_service_id(domain, project, "api"),
        env_vars={
            "NODE_ENV": "production",
            "DATABASE_URL": get_database_url(domain, project),
        },
    )

    # 2. Wait for backend to complete
    backend_final = await harness.wait_for_completion(
        backend_result.deployment_id,
        timeout_seconds=600,
    )

    if backend_final.status != DeploymentStatus.SUCCESS:
        raise Exception(f"Backend deployment failed: {backend_final.error_message}")

    # 3. Deploy frontend to Cloudflare Pages
    frontend_result = await harness.deploy_to_cloudflare(
        project=project,
        directory=f"{domain}/{project}/frontend/dist",
    )

    # 4. Wait for frontend to complete
    frontend_final = await harness.wait_for_completion(
        frontend_result.deployment_id,
        timeout_seconds=600,
    )

    if frontend_final.status != DeploymentStatus.SUCCESS:
        # Rollback backend
        await harness.rollback(backend_result.deployment_id)
        raise Exception(f"Frontend deployment failed: {frontend_final.error_message}")

    return {
        "backend_url": backend_final.url,
        "frontend_url": frontend_final.url,
    }
```

### Monitoring Deployment Progress

```python
async def deploy_with_progress(harness, deployment_id):
    """Monitor deployment with progress updates."""
    while True:
        status = await harness.get_deployment_status(deployment_id)

        print(f"Status: {status.status.value}")

        if status.status in (
            DeploymentStatus.SUCCESS,
            DeploymentStatus.FAILED,
            DeploymentStatus.CANCELLED,
        ):
            return status

        await asyncio.sleep(5)
```

## Related Components

- **OrchestrationHarness** - Uses DeploymentHarness for pipeline deployments
- **NotificationHarness** - Send deployment notifications
- **SessionStateManager** - Track deployment history
- **HumanGateHarness** - Approval gates for production deployments

## Future Enhancements

Planned improvements:

- [ ] Direct file upload for Cloudflare Pages (currently triggers via API)
- [ ] Vercel deployment support
- [ ] GitHub Actions deployment support
- [ ] Health check verification after deployment
- [ ] Automatic DNS configuration
- [ ] Blue/green deployment strategies
- [ ] Canary deployments
- [ ] Deployment metrics and analytics

## License

MIT License - Part of the FORGE autonomous development harness.
