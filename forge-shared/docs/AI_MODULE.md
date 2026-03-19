# AI Module Documentation (`forge_shared.ai`)

The `forge_shared.ai` module provides a unified interface for interacting with various Large Language Model (LLM) providers, including Anthropic, OpenAI, and OpenRouter. It features robust retry logic, graceful failover, and utilities for parsing structured responses.

## Installation

Ensure you have the required provider packages installed:

```bash
pip install "forge-shared[ai]"
# Or manually:
pip install anthropic openai
```

---

## LLM Client API

### `LLMClient`

The main class for interacting with LLM providers.

#### Constructor

```python
LLMClient(
    provider: Union[LLMProvider, str],
    api_key: str,
    base_url: Optional[str] = None,
    default_model: Optional[str] = None,
    retry_config: Optional[RetryConfig] = None,
    fallback_provider: Optional[Union[LLMProvider, str]] = None,
    fallback_api_key: Optional[str] = None,
    fallback_base_url: Optional[str] = None,
)
```

**Parameters:**

- `provider`: Primary provider (`anthropic`, `openai`, `openrouter`).
- `api_key`: API key for the primary provider.
- `base_url`: Optional custom base URL (e.g., for local LLM proxies or OpenRouter).
- `default_model`: Default model ID to use if not specified in `generate()`.
- `retry_config`: `RetryConfig` instance for transient error handling.
- `fallback_provider`: Optional secondary provider to use if the primary exhausts all retries.
- `fallback_api_key`: API key for the fallback provider.
- `fallback_base_url`: Base URL for the fallback provider.

#### `generate()` (Async)

Generates a text completion.

```python
async def generate(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    **kwargs: Any,
) -> str
```

**Parameters:**

- `prompt`: The user message/instruction.
- `system_prompt`: Optional system instruction (handled differently per provider).
- `model`: Model ID (overrides `default_model`).
- `max_tokens`: Maximum tokens to generate (default: 2048).
- `temperature`: Sampling temperature (default: 0.7).
- `**kwargs`: Additional provider-specific parameters passed directly to the underlying client.

---

### `RetryConfig` (DataClass)

Configures exponential backoff for failed requests.

**Attributes:**

- `max_retries`: Number of attempts before failing (default: 3).
- `base_delay`: Initial delay in seconds (default: 1.0).
- `max_delay`: Maximum delay between retries (default: 10.0).
- `exponential_base`: Multiplier for exponential backoff (default: 2.0).

---

### `LLMProvider` (Enum)

Supported provider identifiers:

- `LLMProvider.ANTHROPIC` (`"anthropic"`)
- `LLMProvider.OPENAI` (`"openai"`)
- `LLMProvider.OPENROUTER` (`"openrouter"`)

---

### `create_client()`

Factory function for easy client initialization.

```python
def create_client(provider: str, api_key: str, **kwargs: Any) -> LLMClient
```

---

## Utilities API

### `extract_json()`

Safely extracts and parses JSON from LLM responses, automatically handling Markdown code blocks.

```python
def extract_json(text: Optional[str]) -> Optional[Union[Dict[str, Any], list]]
```

**Features:**
- Handles `None` or empty string inputs gracefully.
- Detects and strips ` ```json ... ``` ` and ` ``` ... ``` ` Markdown wrappers.
- Returns `None` if parsing fails (logging the error).

---

## Usage Examples

### Basic Completion

```python
from forge_shared.ai import create_client

client = create_client(
    provider="anthropic",
    api_key="your-api-key",
    default_model="claude-3-5-sonnet-20241022"
)

async def main():
    response = await client.generate("Tell me a joke about AI.")
    print(response)
```

### Structured Output (JSON)

```python
from forge_shared.ai import create_client, extract_json

client = create_client(
    provider="openrouter",
    api_key="your-key",
    default_model="google/gemini-2.0-flash-001"
)

async def get_user_data():
    prompt = "Create a JSON object for a user named Alice, aged 30."
    response = await client.generate(prompt)
    
    data = extract_json(response)
    if data:
        print(f"Name: {data['name']}, Age: {data['age']}")
```

### High-Availability (With Failover)

```python
from forge_shared.ai import LLMClient, RetryConfig

config = RetryConfig(max_retries=5)
client = LLMClient(
    provider="openai",
    api_key="primary-key",
    default_model="gpt-4o",
    retry_config=config,
    fallback_provider="anthropic",
    fallback_api_key="secondary-key"
)

# If OpenAI fails 5 times, it will automatically try Anthropic
response = await client.generate("Explain quantum computing.")
```
