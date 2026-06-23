"""The shape of a single network identity, plus the abstract interface every identity source (proxy lists, paid services, whatever comes later) has to implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Identity:
    source: str
    proxy_url: str
    proxy_type: str


class IdentitySource(ABC):
    @abstractmethod
    async def build(self) -> Identity | None:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
