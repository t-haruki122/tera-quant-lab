"""ユーザー・リスト・タグ関連のPydanticスキーマ"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt

from app.schemas.backtest import RunKind, RunName, RunNote
from app.schemas.investments import AlertKind, Note, Threshold
from app.schemas.stock import StockSnapshot
from app.validation import Symbol

# --- ユーザー ---

class UserRegisterRequest(BaseModel):
    """ユーザー登録リクエスト"""
    username: str
    password: str


class UserLoginRequest(BaseModel):
    """ログインリクエスト"""
    username: str
    password: str


class ConfirmPasswordRequest(BaseModel):
    current_password: str


class ChangePasswordRequest(ConfirmPasswordRequest):
    new_password: str


class UserResponse(BaseModel):
    """ユーザーレスポンス"""
    id: int
    username: str
    access_token: str
    token_type: Literal["bearer"] = "bearer"


# --- リスト ---

class ListCreateRequest(BaseModel):
    """リスト作成リクエスト"""
    name: str


class ListUpdateRequest(BaseModel):
    """リスト更新リクエスト"""
    name: str


class ListItemAddRequest(BaseModel):
    """リストアイテム追加リクエスト"""
    symbol: Symbol
    tags: list[str] = []


class TagUpdateRequest(BaseModel):
    """タグ更新リクエスト"""
    tags: list[str]


def validate_shares(value: object) -> float:
    message = "株数・口数は0より大きく10億以下の数値を小数第4位までで入力してください"
    if type(value) not in (int, float):
        raise ValueError(message)
    shares = Decimal(str(value))
    if not shares.is_finite() or not 0 < shares <= 10**9 or shares != round(shares, 4):
        raise ValueError(message)
    return float(shares)


CashJPY = Annotated[int, Field(strict=True, ge=0, le=10**13)]


class SharesRequest(BaseModel):
    shares: Annotated[float, BeforeValidator(validate_shares)] | None


class CashRequest(BaseModel):
    cash_jpy: CashJPY | None


class ListItemResponse(BaseModel):
    """リストアイテムレスポンス"""
    symbol: str
    tags: list[str]
    shares: float | None = None


class ListResponse(BaseModel):
    """リストレスポンス"""
    id: int
    name: str
    created_at: str
    updated_at: str


class ListDetailResponse(BaseModel):
    """リスト詳細レスポンス（アイテム含む）"""
    id: int
    name: str
    items: list[ListItemResponse]
    cash_jpy: int | None = None
    created_at: str
    updated_at: str


class ListsResponse(BaseModel):
    """リスト一覧レスポンス"""
    lists: list[ListResponse]
    default_list_id: int | None = None


class MessageResponse(BaseModel):
    """汎用メッセージレスポンス"""
    message: str


class StockMemoRequest(BaseModel):
    """銘柄メモ保存リクエスト"""
    memo: str | None = None


class StockMemoResponse(BaseModel):
    """銘柄メモレスポンス"""
    symbol: str
    memo: str | None = None
    updated_at: str | None = None


class ListSummaryItem(ListItemResponse, StockSnapshot):
    memo: str | None = None
    value_jpy: float | None = None
    weight_pct: float | None = None
    warning: str | None = None


class PortfolioWeight(BaseModel):
    label: str
    value_jpy: float
    pct: float | None


class HoldingWeight(BaseModel):
    symbol: str
    pct: float


class Concentration(BaseModel):
    largest: HoldingWeight | None
    top_5_pct: float | None
    holding_count: int


class PortfolioFX(BaseModel):
    rate: float
    stale: bool = False
    cached_at: str | None = None


class ExcludedHolding(BaseModel):
    symbol: str
    reason: str


class ListPortfolio(BaseModel):
    total_value_jpy: float
    stocks_value_jpy: float
    funds_value_jpy: float
    cash_jpy: int
    cash_ratio_pct: float | None
    by_region: list[PortfolioWeight]
    by_currency: list[PortfolioWeight]
    by_asset_class: list[PortfolioWeight]
    by_sector: list[PortfolioWeight]
    concentration: Concentration
    usd_jpy: PortfolioFX | None
    excluded: list[ExcludedHolding]


class ListSummaryResponse(ListResponse):
    items: list[ListSummaryItem]
    cash_jpy: int | None = None
    portfolio: ListPortfolio | None = None


class BackupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BackupItem(BackupModel):
    symbol: str
    tags: list[str]
    added_at: datetime | None
    # A migrated legacy holding may exceed the limits on new edits.
    shares: Annotated[float, Field(gt=0, allow_inf_nan=False)] | None = None


class BackupList(BackupModel):
    name: str
    items: list[BackupItem]
    cash_jpy: CashJPY | None = None


class BackupMemo(BackupModel):
    symbol: str
    memo: str | None
    updated_at: datetime


class BackupHolding(BackupModel):
    symbol: Symbol
    shares: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    avg_cost: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    acquired_on: date | None
    note: Note | None
    created_at: datetime
    updated_at: datetime


class BackupAlert(BackupModel):
    symbol: Symbol
    kind: AlertKind
    threshold: Threshold
    enabled: bool
    note: Note | None
    created_at: datetime
    last_triggered_at: datetime | None
    last_triggered_market_date: date | None = None
    trigger_count: int = Field(ge=0, le=2**63 - 1)
    armed: bool


class BackupBacktestRun(BackupModel):
    name: RunName
    kind: RunKind
    request: dict[str, Any]
    result: dict[str, Any]
    created_at: datetime
    note: RunNote | None


class UserBackupData(BackupModel):
    version: StrictInt
    exported_at: datetime
    username: str
    lists: list[BackupList]
    memos: list[BackupMemo]
    alerts: list[BackupAlert] = Field(default_factory=list)
    backtest_runs: list[BackupBacktestRun] = Field(default_factory=list)


class UserBackup(UserBackupData):
    holdings: list[BackupHolding] = Field(default_factory=list)


class ImportCounts(BaseModel):
    lists: int = 0
    items: int = 0
    tags: int = 0
    memos: int = 0
    holdings: int = 0
    alerts: int = 0
    backtest_runs: int = 0


class SkippedEntry(BaseModel):
    entry: str
    reason: str


class ImportResult(BaseModel):
    imported: ImportCounts
    skipped: list[SkippedEntry]
