# Service Specification: Configuration Management

## Overview
Config management provides a type-safe way to handle application settings using Pydantic.

## Base Class: `BaseConfig`

### Features
- **Environment Loading**: Automatically loads from `.env` and environment variables.
- **Validation**: Strict type checking and validation.
- **Common Settings**:
  - `app_name`, `environment`, `debug`
  - `host`, `port`
  - `database_url`, `redis_url`
  - `posthog_api_key`, `jwt_secret`

### Utility Methods
- `is_production`: Returns `True` if environment is "production".
- `get_database_url(async_driver=True)`: Returns the DB URL formatted for the requested driver.

## Usage Pattern
```python
from forge_shared.config import BaseConfig, get_config

class MyProjectSettings(BaseConfig):
    custom_val: int = 42

settings = get_config(MyProjectSettings)
```
