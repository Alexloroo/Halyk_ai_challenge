from collections.abc import Callable
from decimal import Decimal
from operator import eq, ge, gt, le, lt, ne

Numeric = Decimal | int

_COMPARATORS: dict[str, Callable[[Numeric, Numeric], bool]] = {
    "<": lt,
    "<=": le,
    ">": gt,
    ">=": ge,
    "==": eq,
    "!=": ne,
}


def compare(value: Numeric, comparator: str, threshold: Numeric) -> bool:
    """Return True when a calculated value satisfies the covenant condition."""
    try:
        operation = _COMPARATORS[comparator]
    except KeyError as exc:
        raise ValueError(f"Unsupported comparator: {comparator}") from exc
    return operation(value, threshold)
