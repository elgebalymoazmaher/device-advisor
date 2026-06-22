from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Identity:
    source: str
    proxy_url: str
    proxy_type: str


class IdentitySource(ABC):

    @abstractmethod
    async def build(self) -> Identity | None:
        ...

    @abstractmethod
    async def health(self) -> bool:
        ...

    @abstractmethod
    async def close(self):
        ...
