"""The Source protocol all holdings adapters must satisfy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from allocator.model import Holding


@runtime_checkable
class Source(Protocol):
    """A holdings data source. Implementations return all holdings they track."""

    name: str
    """Short identifier (`monarch`, `manual`, ...) used to tag Holding.source."""

    def fetch(self) -> list[Holding]:
        """Return the current set of holdings from this source.

        Implementations should raise a subclass of `Exception` with a helpful
        message if the source cannot be reached; callers catch and surface it
        to the user. Credentials must never be included in the exception text.
        """
        ...
