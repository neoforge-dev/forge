"""Parsing utilities for LLM responses."""

import json
import logging
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)


def extract_json(text: Optional[str]) -> Optional[Union[Dict[str, Any], list]]:
    """Extract and parse JSON from text, handling markdown code blocks.

    Args:
        text: Raw text response from LLM (can be None)

    Returns:
        Parsed JSON object/list, or None if parsing fails
    """
    if text is None or not text.strip():
        return None

    cleaned_text = text.strip()
    
    # Handle markdown code blocks
    if "```json" in cleaned_text:
        try:
            cleaned_text = cleaned_text.split("```json")[1].split("```")[0].strip()
        except IndexError:
            pass  # Fallback to original text if splitting fails
    elif "```" in cleaned_text:
        try:
            cleaned_text = cleaned_text.split("```")[1].split("```")[0].strip()
        except IndexError:
            pass

    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON: {e}. Raw text: {text[:100]}...")
        return None
