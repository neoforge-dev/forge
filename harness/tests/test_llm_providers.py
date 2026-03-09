import sys
from unittest.mock import MagicMock, patch

import pytest

# Mock openai and google.generativeai modules before importing llm_provider
sys.modules["openai"] = MagicMock()
sys.modules["google.generativeai"] = MagicMock()
sys.modules["claude_code_sdk"] = MagicMock()

from forge_harness.llm_provider import (
    ClaudeCodeProvider,
    GeminiProvider,
    OpenAIProvider,
    get_provider,
)


def test_get_provider_openai():
    provider = get_provider("openai")
    assert isinstance(provider, OpenAIProvider)


def test_get_provider_gemini():
    provider = get_provider("gemini")
    assert isinstance(provider, GeminiProvider)


def test_get_provider_claude():
    provider = get_provider("claude")
    assert isinstance(provider, ClaudeCodeProvider)


def test_get_provider_case_insensitive():
    provider = get_provider("OpenAI")
    assert isinstance(provider, OpenAIProvider)


def test_get_provider_default():
    # If unknown, it should return ClaudeCodeProvider (or raise error based on implementation)
    # The current implementation falls back to ClaudeCodeProvider
    provider = get_provider("unknown_provider")
    assert isinstance(provider, ClaudeCodeProvider)


@pytest.mark.asyncio
async def test_openai_generate_content_mock():
    provider = OpenAIProvider()
    provider.client.chat.completions.create = MagicMock()

    # Mock return value
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Hello from OpenAI"

    # Async mock setup
    async def async_mock(*args, **kwargs):
        return mock_response

    provider.client.chat.completions.create.side_effect = async_mock

    response = await provider.generate_content(
        "Test prompt",
        MagicMock(model="gpt-4", system_prompt=None, max_tokens=None, temperature=0.7),
    )
    assert response == "Hello from OpenAI"


@pytest.mark.asyncio
async def test_gemini_generate_content_mock():
    with patch("forge_harness.llm_provider.os.environ.get", return_value="fake_key"):
        provider = GeminiProvider()
        # Mock the GenerativeModel instance
        mock_model = MagicMock()
        provider.genai.GenerativeModel.return_value = mock_model

        # Mock generate_content_async response
        mock_response = MagicMock()
        mock_response.text = "Hello from Gemini"

        async def async_mock(*args, **kwargs):
            return mock_response

        mock_model.generate_content_async.side_effect = async_mock

        response = await provider.generate_content(
            "Test prompt",
            MagicMock(model="gemini-pro", system_prompt=None, max_tokens=None, temperature=0.7),
        )
        assert response == "Hello from Gemini"
