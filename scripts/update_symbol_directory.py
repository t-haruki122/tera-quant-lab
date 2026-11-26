"""JPX・Nasdaq Trader の銘柄ディレクトリを今すぐ更新します。"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database  # noqa: E402
from app.services.symbol_directory import refresh_symbol_directory  # noqa: E402


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        await database.upgrade_db()
        counts = await refresh_symbol_directory()
    finally:
        await database.engine.dispose()
    for market in ("JP", "US"):
        print(f"{market}: {counts[market]} 件更新" if market in counts else f"{market}: 更新失敗（既存データを保持）")
    return 0 if len(counts) == 2 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
