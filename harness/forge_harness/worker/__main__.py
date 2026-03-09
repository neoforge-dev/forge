#!/usr/bin/env python3
"""Run the v3 WebSocket worker."""

import asyncio

from .websocket_client import main

if __name__ == "__main__":
    asyncio.run(main())
