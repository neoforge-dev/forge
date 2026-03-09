"""
Greet module for FORGE project.

This module provides a greet function that returns a personalized greeting.
"""


def greet(name: str) -> str:
    """
    Return a greeting for the given name.

    Args:
        name: The name to greet.

    Returns:
        str: A greeting in the format 'Hello, {name}!'
    """
    return f"Hello, {name}!"
