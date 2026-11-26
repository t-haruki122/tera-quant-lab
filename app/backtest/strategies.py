"""Typed strategy registry. All signals use only the current and earlier closes."""

from typing import Any, cast

import pandas as pd

from .pit import eps_for_bars, number


class Strategy:
    name = "base"
    max_per = None

    def prepare(self, price_df: pd.DataFrame, context: dict[str, Any]) -> pd.DataFrame:
        raise NotImplementedError


class SmaCrossPerStrategy(Strategy):
    name = "sma_cross"

    def __init__(self, short_window=20, long_window=60, max_per=None):
        self.params = validate_params(self.name, dict(short_window=short_window, long_window=long_window, max_per=max_per))
        self.short_window = self.params["short_window"]
        self.long_window = self.params["long_window"]
        self.max_per = self.params["max_per"]

    def prepare(self, price_df, context):
        df = price_df.copy()
        close = cast(pd.Series, df["close"])  # A single named price column is a Series.
        # Rolling.mean preserves the Series input, though pandas types it for several containers.
        short = cast(pd.Series, close.rolling(self.short_window).mean())
        long = cast(pd.Series, close.rolling(self.long_window).mean())
        df["sma_short"], df["sma_long"] = short, long
        df["signal"] = 0
        df.loc[(short > long) & (short.shift() <= long.shift()), "signal"] = 1
        df.loc[(short < long) & (short.shift() >= long.shift()), "signal"] = -1
        return apply_per(df, context, self.max_per)


def apply_per(df, context, max_per):
    if max_per is not None:
        df["eps"] = eps_for_bars(df["date"], context.get("financial_history", {}), context.get("eps_lag_days", 90))
        df["per"] = df["close"] / df["eps"].where(df["eps"] > 0)
        df.loc[(df["signal"] == 1) & ~((df["eps"] > 0) & (df["per"] <= max_per)), "signal"] = 0
    return df


def wilder_rsi(close, period):
    changes = close.diff()
    result = pd.Series(float("nan"), index=close.index)
    if len(close) <= period:
        return result
    gain = changes.iloc[1:period + 1].clip(lower=0).mean()
    loss = -changes.iloc[1:period + 1].clip(upper=0).mean()
    for i in range(period, len(close)):
        if i > period:
            gain = (gain * (period - 1) + max(changes.iloc[i], 0)) / period
            loss = (loss * (period - 1) + max(-changes.iloc[i], 0)) / period
        result.iloc[i] = 100 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return result


class RegisteredStrategy(Strategy):
    def __init__(self, name, params):
        self.name, self.params = name, validate_params(name, params)
        self.max_per = self.params["max_per"]

    def prepare(self, price_df, context):
        df, p = price_df.copy(), self.params
        close = df["close"]
        if self.name == "rsi_reversion":
            df["rsi"] = wilder_rsi(close, p["period"])
            buy, sell = df["rsi"] < p["lower"], df["rsi"] > p["upper"]
        elif self.name == "bollinger":
            df["middle"] = close.rolling(p["window"]).mean()
            deviation = close.rolling(p["window"]).std(ddof=0) * p["std_dev"]
            df["lower"], df["upper"] = df["middle"] - deviation, df["middle"] + deviation
            buy, sell = close < df["lower"], close >= df[p["exit"]]
        else:
            df["roc"] = close / close.shift(p["period"]) - 1
            buy, sell = df["roc"] > p["threshold"], df["roc"] < p["exit_threshold"]
        df["signal"] = 0
        df.loc[buy, "signal"], df.loc[sell, "signal"] = 1, -1
        return apply_per(df, context, self.max_per)


# (Python type, default). max_per is optional for every strategy.
REGISTRY = {
    "sma_cross": {"short_window": (int, 20), "long_window": (int, 60)},
    "rsi_reversion": {"period": (int, 14), "lower": (float, 30.0), "upper": (float, 70.0)},
    "bollinger": {"window": (int, 20), "std_dev": (float, 2.0), "exit": (str, "middle")},
    "momentum": {"period": (int, 20), "threshold": (float, 0.0), "exit_threshold": (float, 0.0)},
}
for _schema in REGISTRY.values():
    _schema["max_per"] = (float, None)

PARAM_DESCRIPTIONS = {
    "short_window": "短期移動平均のバー数",
    "long_window": "長期移動平均のバー数",
    "period": "指標の計算に使用するバー数",
    "lower": "RSIの買い閾値",
    "upper": "RSIの売り閾値",
    "window": "ボリンジャーバンドの計算に使用するバー数",
    "std_dev": "バンド幅の標準偏差倍率",
    "exit": "売り判定に使うバンド",
    "threshold": "買い判定の変化率（0.05 = 5%）",
    "exit_threshold": "売り判定の変化率（0.05 = 5%）",
    "max_per": "買いを許可するPERの上限（空欄で無効）",
}
POSITIVE_PARAMS = {"short_window", "long_window", "period", "window", "std_dev", "max_per"}
EXIT_CHOICES = ("middle", "upper")


def param_schema(name):
    result = []
    for key, (kind, default) in REGISTRY[name].items():
        field: dict[str, Any] = dict(name=key, type=kind.__name__, default=default, description=PARAM_DESCRIPTIONS[key])
        if key in POSITIVE_PARAMS:
            field.update(min=1 if kind is int else 0)
            if kind is float:
                field["exclusive_min"] = True
        elif key in ("lower", "upper"):
            field.update(min=0, max=100)
        elif key == "exit":
            field["choices"] = EXIT_CHOICES
        result.append(field)
    return result


def param_help(name):
    return ", ".join(f"{key}: {kind.__name__} (default={default})" for key, (kind, default) in REGISTRY[name].items())


def validate_params(name, params=None):
    if name not in REGISTRY:
        raise ValueError(f"不明な戦略 {name!r}。次のいずれかを指定してください: {', '.join(REGISTRY)}")
    schema = REGISTRY[name]
    try:
        extra = set(params or {}) - schema.keys()
        if extra:
            raise ValueError(f"不明なパラメータ: {', '.join(sorted(extra))}")
        values = {}
        for key, (kind, default) in schema.items():
            raw = (params or {}).get(key, default)
            if raw is None:
                if default is not None:
                    raise ValueError(f"{key} は {kind.__name__} 型の値を指定してください")
                values[key] = None
                continue
            try:
                value = kind(raw)
                finite = kind not in (int, float) or number(value) is not None
            except (ValueError, TypeError, OverflowError) as exc:
                raise ValueError(f"{key} は {kind.__name__} 型の値を指定してください") from exc
            if isinstance(raw, bool) or (kind is int and str(raw) != str(value)):
                raise ValueError(f"{key} は {kind.__name__} 型の値を指定してください")
            if not finite:
                raise ValueError(f"{key} は有限の数値を指定してください")
            values[key] = value
        for key in sorted(POSITIVE_PARAMS & values.keys()):
            if key in values and values[key] is not None and values[key] <= 0:
                raise ValueError(f"{key} は正の数値を指定してください")
        if name == "sma_cross" and values["short_window"] >= values["long_window"]:
            raise ValueError("short_window は long_window より小さい値を指定してください")
        if name == "rsi_reversion" and not 0 <= values["lower"] < values["upper"] <= 100:
            raise ValueError("0 <= lower < upper <= 100 を満たす値を指定してください")
        if name == "bollinger" and values["exit"] not in EXIT_CHOICES:
            raise ValueError("exit は middle または upper を指定してください")
        if name == "momentum" and values["exit_threshold"] > values["threshold"]:
            raise ValueError("exit_threshold は threshold 以下の値を指定してください")
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}。指定できるパラメータ: {param_help(name)}") from exc
    return values


def make_strategy(name="sma_cross", params=None):
    values = validate_params(name, params)
    return SmaCrossPerStrategy(**values) if name == "sma_cross" else RegisteredStrategy(name, values)


def parse_assignments(items):
    result = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip() or not value.strip():
            raise ValueError(f"Expected key=value: {item!r}")
        if key.strip() in result:
            raise ValueError(f"Duplicate param: {key.strip()}")
        result[key.strip()] = value.strip()
    return result
