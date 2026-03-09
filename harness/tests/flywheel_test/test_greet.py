"""Test for the greet module created by flywheel."""


def test_greet():
    """Test that greet(name) returns expected greeting format."""
    from tests.flywheel_test.greet import greet

    # Test basic functionality
    result = greet("Alice")
    assert result == "Hello, Alice!"

    # Test with different names
    assert greet("World") == "Hello, World!"
    assert greet("Bob") == "Hello, Bob!"
    assert greet("Test") == "Hello, Test!"


def test_greet_with_empty_string():
    """Test greet with empty string."""
    from tests.flywheel_test.greet import greet

    result = greet("")
    assert result == "Hello, !"


def test_greet_return_type():
    """Test that greet returns a string."""
    from tests.flywheel_test.greet import greet

    result = greet("TypeTest")
    assert isinstance(result, str)
