"""Test for the hello module created by flywheel."""


def test_hello():
    """Test that hello() returns expected greeting."""
    from tests.flywheel_test.hello import hello

    result = hello()
    assert result == "Hello, Flywheel!"
