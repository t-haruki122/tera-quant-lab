"""Run the MCP server over stdio."""

import asyncio

from tera_mcp.client import Settings, TQLClient
from tera_mcp.server import create_server


async def main() -> None:
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    async with TQLClient(settings) as client:
        await create_server(client).run_stdio_async()


if __name__ == "__main__":
    asyncio.run(main())
