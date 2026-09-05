"""Console entry point for the bridge daemon."""
from __future__ import annotations

import asyncio
import contextlib


def cli() -> None:
    """Run the bridge until interrupted.

    The import is deferred because :mod:`bridge_for_agents.bridge` reads
    ``TG_BOT_TOKEN`` and friends from the environment at import time.
    """
    from .bridge import main

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
