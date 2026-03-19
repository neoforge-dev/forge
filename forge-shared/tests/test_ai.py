"Tests for forge_shared.ai module."

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from forge_shared.ai.parsing import extract_json
from forge_shared.ai.client import LLMClient, LLMProvider, create_client

# --- Parsing Tests ---

def test_extract_json_clean():
    text = '{"key": "value"}'
    assert extract_json(text) == {"key": "value"}

def test_extract_json_markdown():
    text = 'Here is the data:\n```json\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}

def test_extract_json_markdown_no_lang():
    text = '```\n{"key": "value"}\n```'
    assert extract_json(text) == {"key": "value"}

def test_extract_json_invalid():
    text = 'Not JSON'
    assert extract_json(text) is None

def test_extract_json_empty():
    assert extract_json("") is None

def test_extract_json_none():
    """Test None input returns None."""
    assert extract_json(None) is None

def test_extract_json_nested():
    """Test nested JSON extraction."""
    text = '```json\n{"outer": {"inner": [1, 2, 3]}}\n```'
    result = extract_json(text)
    assert result == {"outer": {"inner": [1, 2, 3]}}

def test_extract_json_array():
    """Test JSON array extraction."""
    text = '```json\n[{"id": 1}, {"id": 2}]\n```'
    result = extract_json(text)
    assert result == [{"id": 1}, {"id": 2}]

def test_extract_json_malformed_markdown():
    """Test malformed markdown (unclosed block)."""
    text = '```json\n{"key": "value"}'  # Missing closing ```
    # Should fallback and try to parse
    result = extract_json(text)
    # May return None or parsed value depending on implementation
    assert result is None or result == {"key": "value"}

def test_extract_json_with_text_before_after():
    """Test JSON with surrounding text."""
    text = 'Here is the result:\n```json\n{"status": "ok"}\n```\nDone.'
    assert extract_json(text) == {"status": "ok"}

def test_extract_json_whitespace():
    """Test whitespace-only input."""
    assert extract_json("   \n\t  ") is None

# --- Client Tests ---

@pytest.fixture
def mock_anthropic():
    with patch("forge_shared.ai.client.AsyncAnthropic") as mock:
        yield mock

@pytest.fixture
def mock_openai():
    with patch("forge_shared.ai.client.AsyncOpenAI") as mock:
        yield mock

@pytest.mark.asyncio
async def test_anthropic_client_generate(mock_anthropic):
    # Setup mock
    mock_instance = mock_anthropic.return_value
    mock_response = MagicMock()
    mock_content = MagicMock()
    mock_content.text = "Generated text"
    mock_response.content = [mock_content]
    mock_instance.messages.create = AsyncMock(return_value=mock_response)

    client = create_client(
        provider="anthropic",
        api_key="test-key",
        default_model="claude-3"
    )

    result = await client.generate("Hello")
    
    assert result == "Generated text"
    mock_instance.messages.create.assert_called_once()
    call_kwargs = mock_instance.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-3"
    assert call_kwargs["messages"] == [{"role": "user", "content": "Hello"}]

@pytest.mark.asyncio
async def test_openai_client_generate(mock_openai):
    # Setup mock
    mock_instance = mock_openai.return_value
    mock_response = MagicMock()
    mock_message = MagicMock()
    mock_message.content = "Generated text"
    mock_response.choices = [MagicMock(message=mock_message)]
    mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    client = create_client(
        provider="openai",
        api_key="test-key",
        default_model="gpt-4"
    )

    result = await client.generate("Hello", system_prompt="Be nice")
    
    assert result == "Generated text"
    mock_instance.chat.completions.create.assert_called_once()
    call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4"
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "Be nice"},
        {"role": "user", "content": "Hello"}
    ]

def test_invalid_provider():
    with pytest.raises(ValueError):
        create_client(provider="invalid", api_key="key")

@pytest.mark.asyncio
async def test_missing_model(mock_anthropic):
    client = create_client(provider="anthropic", api_key="key") # No default model
    
    with pytest.raises(ValueError, match="Model must be specified"):
        await client.generate("Hello")