"""J-Quants の同期を実行するか、保存済みの進捗を表示します。"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.middleware import TokenRedactingFormatter  # noqa: E402
from app.services.jquants_sync import SyncResult, sync_jquants, sync_status  # noqa: E402


async def main(args: argparse.Namespace) -> int:
    handler = logging.StreamHandler()
    handler.setFormatter(TokenRedactingFormatter("%(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()
    try:
        if args.status:
            async with database.AsyncSessionLocal() as db:
                print(json.dumps(await sync_status(db, settings), default=str, ensure_ascii=False, indent=2))
            return 0
        if settings.jquants_active:
            await database.upgrade_db()
        result = await sync_jquants(settings, dates_from=args.dates_from, dates_to=args.dates_to)
        if result is SyncResult.DISABLED:
            print("J-Quants が無効です。API キーと JQUANTS_ENABLED を確認してください。")
        elif result is SyncResult.BUSY:
            print("別の処理が J-Quants を同期しています。")
        elif result is SyncResult.COMPLETE:
            print("J-Quants の同期が完了しました。")
            return 0
        return 1
    except Exception:
        logging.error("J-Quants の同期用データベースを開けませんでした。DATABASE_URL とマイグレーションを確認してください。")
        return 1
    finally:
        await database.engine.dispose()


def parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return parsed
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("日付は YYYY-MM-DD 形式で指定してください")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dates-from", type=parse_date, help="再取得の開始日 (YYYY-MM-DD)")
    parser.add_argument("--dates-to", type=parse_date, help="再取得の終了日 (YYYY-MM-DD)")
    parser.add_argument("--status", action="store_true", help="保存済みの同期状況を表示")
    args = parser.parse_args()
    if args.status and (args.dates_from or args.dates_to):
        parser.error("--status と日付指定は同時に使用できません")
    if args.dates_from and args.dates_to and args.dates_from > args.dates_to:
        parser.error("取得開始日は終了日以前にしてください")
    raise SystemExit(asyncio.run(main(args)))
