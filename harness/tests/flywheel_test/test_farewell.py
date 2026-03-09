"""Test for the farewell module created by flywheel."""


def test_farewell():
    """Test that farewell() returns expected farewell message."""
    from tests.flywheel_test.farewell import farewell

    result = farewell()
    assert result == "Goodbye from FORGE!"
