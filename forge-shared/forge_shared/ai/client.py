"""Unified LLM Client for Anthropic and OpenAI-compatible providers.

Includes retry logic and provider failover for graceful degradation.
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Union

try:
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI
except ImportError:
    # Handle optional dependencies gracefully
    AsyncAnthropic = None  # type: ignore
    AsyncOpenAI = None  # type: ignore

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    base_delay: float = 1.0  # seconds
    max_delay: float = 10.0  # seconds
    exponential_base: float = 2.0


class LLMClient:
    """Unified client for interacting with LLM providers.

    Features:
    - Support for Anthropic, OpenAI, and OpenRouter providers
    - Retry logic with exponential backoff
    - Fallback provider support for graceful degradation
    """

    def __init__(
        self,
        provider: Union[LLMProvider, str],
        api_key: str,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        retry_config: Optional[RetryConfig] = None,
        fallback_provider: Optional[Union[LLMProvider, str]] = None,
        fallback_api_key: Optional[str] = None,
        fallback_base_url: Optional[str] = None,
    ):
        """Initialize LLM client.

        Args:
            provider: The LLM provider (anthropic, openai, openrouter)
            api_key: API key for the provider
            base_url: Optional base URL (required for some OpenAI-compatible providers)
            default_model: Default model to use if not specified in generate calls
            retry_config: Configuration for retry behavior (default: 3 retries)
            fallback_provider: Optional fallback provider if primary fails
            fallback_api_key: API key for fallback provider
            fallback_base_url: Base URL for fallback provider (if openrouter)
        """
        self.provider = LLMProvider(provider)
        self.api_key = api_key
        self.default_model = default_model
        self.retry_config = retry_config or RetryConfig()
        self._client: Any = None

        # Fallback configuration
        self.fallback_provider: Optional[LLMProvider] = None
        self._fallback_client: Any = None

        # Initialize primary provider
        self._client = self._create_provider_client(
            self.provider, api_key, base_url
        )

        # Initialize fallback provider if configured
        if fallback_provider and fallback_api_key:
            self.fallback_provider = LLMProvider(fallback_provider)
            self._fallback_client = self._create_provider_client(
                self.fallback_provider,
                fallback_api_key,
                fallback_base_url
            )

    def _create_provider_client(
        self,
        provider: LLMProvider,
        api_key: str,
        base_url: Optional[str]
    ) -> Any:
        """Create a provider client instance."""
        if provider == LLMProvider.ANTHROPIC:
            if not AsyncAnthropic:
                raise ImportError("anthropic package is required. Install with 'pip install anthropic'")
            return AsyncAnthropic(api_key=api_key)

        elif provider == LLMProvider.OPENROUTER:
            if not AsyncOpenAI:
                raise ImportError("openai package is required. Install with 'pip install openai'")
            return AsyncOpenAI(
                api_key=api_key,
                base_url=base_url or "https://openrouter.ai/api/v1",
            )

        elif provider == LLMProvider.OPENAI:
            if not AsyncOpenAI:
                raise ImportError("openai package is required. Install with 'pip install openai'")
            return AsyncOpenAI(api_key=api_key, base_url=base_url)

        raise ValueError(f"Unsupported provider: {provider}")

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        **kwargs: Any,
    ) -> str:
        """Generate text from a prompt with retry and failover.

        Args:
            prompt: The user prompt
            system_prompt: Optional system instruction
            model: Model to use (overrides default)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            **kwargs: Additional provider-specific arguments

        Returns:
            Generated text content
        """
        target_model = model or self.default_model
        if not target_model:
            raise ValueError("Model must be specified either in init or generate call")

        last_error: Optional[Exception] = None

        # Try primary provider with retries
        for attempt in range(self.retry_config.max_retries):
            try:
                return await self._call_provider(
                    client=self._client,
                    provider=self.provider,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=target_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
            except Exception as e:
                last_error = e
                delay = min(
                    self.retry_config.base_delay * (self.retry_config.exponential_base ** attempt),
                    self.retry_config.max_delay,
                )
                logger.warning(
                    f"LLM generation attempt {attempt + 1} failed ({self.provider.value}): {e}. "
                    f"Retrying in {delay:.1f}s..."
                )
                if attempt < self.retry_config.max_retries - 1:
                    await asyncio.sleep(delay)

        # Try fallback provider if configured
        if self.fallback_provider and self._fallback_client:
            logger.info(f"Primary provider exhausted, trying fallback: {self.fallback_provider.value}")
            try:
                return await self._call_provider(
                    client=self._fallback_client,
                    provider=self.fallback_provider,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=None,  # Use provider default for fallback
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
            except Exception as e:
                logger.error(f"Fallback provider also failed ({self.fallback_provider.value}): {e}")
                last_error = e

        # All attempts failed
        logger.error(f"All LLM generation attempts failed", exc_info=last_error)
        raise last_error or RuntimeError("LLM generation failed")

    async def _call_provider(
        self,
        client: Any,
        provider: LLMProvider,
        prompt: str,
        system_prompt: Optional[str],
        model: Optional[str],
        max_tokens: int,
        temperature: float,
        **kwargs: Any,
    ) -> str:
        """Call a specific provider."""
        if provider == LLMProvider.ANTHROPIC:
            messages = [{"role": "user", "content": prompt}]

            # Default model for Anthropic
            target_model = model or "claude-3-5-sonnet-20241022"

            response = await client.messages.create(
                model=target_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                system=system_prompt if system_prompt else [],
                **kwargs
            )

            if not response.content or not hasattr(response.content[0], "text"):
                raise ValueError("Empty response from Anthropic")
            return response.content[0].text

        elif provider in (LLMProvider.OPENAI, LLMProvider.OPENROUTER):
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Default models
            if provider == LLMProvider.OPENROUTER:
                target_model = model or "google/gemini-2.0-flash-001"
            else:
                target_model = model or "gpt-4o"

            response = await client.chat.completions.create(
                model=target_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=messages,
                **kwargs
            )

            content = response.choices[0].message.content
            if not content:
                raise ValueError(f"Empty response from {provider.value}")
            return content

        else:
            raise ValueError(f"Unsupported provider: {provider}")


def create_client(
    provider: str,
    api_key: str,
    **kwargs: Any
) -> LLMClient:
    """Factory function to create an LLM client.

    Args:
        provider: Provider name (anthropic, openai, openrouter)
        api_key: API key
        **kwargs: Additional arguments for LLMClient (default_model, retry_config,
                  fallback_provider, fallback_api_key, fallback_base_url)

    Returns:
        Configured LLMClient instance
    """
    return LLMClient(provider=provider, api_key=api_key, **kwargs)
