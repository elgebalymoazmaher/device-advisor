"""
Merge all brand files from data/crawl/*.json into data/device_listings.json.
"""

import glob
import json
import logging
import os
import sys

from settings.logging import setup_logging

setup_logging()
log = logging.getLogger(__name__)


def main():
    crawl_dir = os.path.join(os.getcwd(), "data", "crawl")
    out_path = os.path.join(os.getcwd(), "data", "device_listings.json")

    if not os.path.isdir(crawl_dir):
        log.error("No data/crawl/ directory found")
        return 1

    files = sorted(glob.glob(os.path.join(crawl_dir, "*.json")))
    if not files:
        log.error("No brand files found in data/crawl/")
        return 1

    all_devices = []
    for path in files:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("checkpoint_url") is not None:
            log.warning(
                "%s is incomplete (checkpoint %s)",
                os.path.basename(path), data["checkpoint_url"],
            )
        all_devices.extend(data.get("devices", []))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_devices, f, indent=2, ensure_ascii=False)

    log.info("Merged %d brand files into %s (%d devices)", len(files), out_path, len(all_devices))
    return 0


if __name__ == "__main__":
    sys.exit(main())
