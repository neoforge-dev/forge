"""Test for the goodbye module created by flywheel."""


def test_goodbye():
    """Test that goodbye() returns expected farewell message."""
    from tests.flywheel_test.goodbye import goodbye

    result = goodbye()
    assert result == "Goodbye, Flywheel!"
