from dataclasses import dataclass


@dataclass
class DeviceListing:
    name: str
    url: str
    slug: str
    image_url: str
    raw_title: str
