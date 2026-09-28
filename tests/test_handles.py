import pytest
from django.core.exceptions import ValidationError

from actors.handles import normalize_handle, validate_handle


@pytest.mark.parametrize("handle", ["alice", "a_b_1", "abc", "a" * 32])
def test_valid_handles_are_accepted(handle):
    assert validate_handle(handle) == handle


@pytest.mark.parametrize(
    "handle", ["ab", "a" * 33, "bad handle", "bad-handle", "bad.handle", ""]
)
def test_invalid_handles_are_rejected(handle):
    with pytest.raises(ValidationError):
        validate_handle(handle)


@pytest.mark.parametrize("handle", ["instance", "admin", "actors", "nodeinfo"])
def test_reserved_handles_are_rejected(handle):
    with pytest.raises(ValidationError):
        validate_handle(handle)


def test_handles_are_normalized_to_lowercase():
    assert normalize_handle("  Alice  ") == "alice"
    assert validate_handle("ALICE") == "alice"
