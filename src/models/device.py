from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Brand:
    name: str
    slug: str
    url: str
    device_count: int


@dataclass
class DeviceListing:
    name: str
    slug: str
    url: str
    image_url: str
    raw_title: str


@dataclass
class Device:
    model_name: str
    brand: str
    image_url: str
    specs: dict[str, dict[str, str]] = field(default_factory=dict)
