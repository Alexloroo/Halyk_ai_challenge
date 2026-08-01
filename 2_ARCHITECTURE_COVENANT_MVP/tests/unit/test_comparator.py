from decimal import Decimal

import pytest

from halyk_covenants.evaluators.comparator import compare


@pytest.mark.parametrize(
    ("comparator", "equal_result", "lower_result", "higher_result"),
    [
        ("<", False, True, False),
        ("<=", True, True, False),
        (">", False, False, True),
        (">=", True, False, True),
        ("==", True, False, False),
        ("!=", False, True, True),
    ],
)
def test_compare_has_exact_boundary_semantics(
    comparator: str,
    equal_result: bool,
    lower_result: bool,
    higher_result: bool,
) -> None:
    threshold = Decimal("10000000.000000")

    assert compare(threshold, comparator, threshold) is equal_result
    assert compare(Decimal("9999999.999999"), comparator, threshold) is lower_result
    assert compare(Decimal("10000000.000001"), comparator, threshold) is higher_result


def test_compare_rejects_unknown_comparator() -> None:
    with pytest.raises(ValueError, match="Unsupported comparator"):
        compare(Decimal("1"), "~", Decimal("1"))
