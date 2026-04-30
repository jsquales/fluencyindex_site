from collections import Counter
from fractions import Fraction


class Game24Pool:
    def __init__(self, values: list[Fraction]):
        self._counts = Counter(values)

    def has(self, value: Fraction) -> bool:
        return self._counts[value] > 0

    def consume_pair(self, left: Fraction, right: Fraction) -> str | None:
        needed = Counter([left, right])
        for value, count in needed.items():
            available = self._counts[value]
            if available == 0:
                return "INPUT_NOT_AVAILABLE"
            if available < count:
                return "INSUFFICIENT_DUPLICATE_COUNT"
        self._counts.subtract(needed)
        self._counts += Counter()
        return None

    def add(self, value: Fraction) -> None:
        self._counts[value] += 1

    def values(self) -> list[Fraction]:
        return list(self._counts.elements())
