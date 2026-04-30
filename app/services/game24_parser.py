import re
from fractions import Fraction


NUMBER_RE = re.compile(r"^[+-]?(?:(?:\d+(?:\.\d+)?)|(?:\.\d+)|(?:\d+/\d+))$")


def parse_number(raw: str) -> Fraction:
    """Parse one allowed numeric token into an exact Fraction."""
    if not isinstance(raw, str) or not raw:
        raise ValueError("Invalid number format.")
    if any(ch.isspace() for ch in raw):
        raise ValueError("Invalid number format.")
    if not NUMBER_RE.fullmatch(raw):
        raise ValueError("Invalid number format.")
    if "/" in raw:
        numerator, denominator = raw.split("/", 1)
        if int(denominator) == 0:
            raise ValueError("Invalid number format.")
        return Fraction(int(numerator), int(denominator))
    return Fraction(raw)
