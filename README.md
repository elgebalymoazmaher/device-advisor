# device-advisor

Collects device (phone) data from GSMArena: brand list -> per-brand listing
pages -> per-device spec pages. Fetches go through a rotating pool of
proxies so a single source IP isn't hammering the site, with retry/backoff
and on-disk checkpointing so an interrupted run can pick up where it left
off.

## Layout

```
main.py                     entry point: runs the full fetch/crawl pipeline
src/
  shared/                   cross-cutting config, logging, storage helpers
  scraper/
    identity/               proxy sourcing + the IdentityPool that hands them out
    net/                    the proxy-aware HTTP client
    crawl/                  the three pipeline stages + the live terminal dashboard
    parsing/                turns raw HTML into structured rows (no I/O)
tests/                      mirrors the src/ layout above
```

`src/scraper` is the only implemented subpackage today; `recommending` and
`chatting` (per the device-advisor product) would land as siblings later.

## Setup

```
pip install -r requirements.txt
# for running tests / linting:
pip install -e ".[dev]"
```

Requires Python 3.12+.

## Running

```
python main.py        # runs fetch-brands -> crawl-listings -> crawl-specs in sequence
python main.py -v     # same, with debug-level logging
```

There's no subcommand flag to run a single stage on its own from the CLI,
but each stage is an independent, importable async function
(`fetch_brands()`, `crawl_listings()`, `crawl_specs()`) if you want to run
just one from a script or REPL.

Data paths are all under `DATA_DIR` (default: `./data`, overridable via the
`DATA_DIR` env var): `brands.json`, `listings/<brand>.json`,
`checkpoint.json`, `specs/<slug>.json`, `specs/retries.json`. Worker/pool
sizing auto-scales from CPU count and RAM, or can be pinned with the
`DEVICE_ADVISOR_WORKERS` env var.

## Testing

```
pytest
```

Tests are fully offline: HTTP-touching code is exercised through
`httpx.MockTransport` or small fake pool/client doubles, never the real
network. `pyproject.toml` wires up `pytest-asyncio` in auto mode, so async
tests are plain `async def test_...` functions with no decorator needed.

```
ruff check . && ruff format --check .   # lint + formatting
mypy .                                   # type checking
```

A handful of `mypy` warnings about `.get("href", ...)`-style bs4 attribute
access are known stub noise, not real bugs -- see the docstring at the top
of `src/scraper/parsing/specs.py` for why.

## Known no-ops

A few settings in `src/shared/settings.py` (`MAX_CONCURRENT_SPECS`,
`MAX_PAGES_PER_BRAND`, `BAN_DURATION`, `STAGGER_MAX`) and the module
`src/scraper/net/throttle.py` are carried over from an earlier version of
the script and aren't wired into the current pipeline. They're documented
in place rather than deleted, in case a future change wants to use them.
