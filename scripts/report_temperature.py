"""当前各资产温度一览

输出 symbol → 温度 T → 七档状态 → 热度 size，用于回答「主流资产处于什么水平」。

用法：
    python3 scripts/report_temperature.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from core.data import DataManager, US_SYMBOLS, CN_SYMBOLS, GLOBAL_SYMBOLS
from core.strategy.temperature import (
    calc_temperature,
    simulate_symbol_positions,
    bucket_temperature_series,
    TEMP_STATES,
    TEMP_COLORS,
)


def main():
    dm = DataManager()
    symbols = {**US_SYMBOLS, **CN_SYMBOLS, **GLOBAL_SYMBOLS}

    rows = []
    for symbol, desc in symbols.items():
        df = dm.load(symbol, start_date="2018-01-01")
        if df.empty or len(df) < 126:
            rows.append({
                "symbol": symbol,
                "name": desc,
                "T": None,
                "state": "数据不足",
                "size": 0.0,
                "date": "",
            })
            continue

        df = df.reset_index(drop=True)
        close, high, low = df["close"], df["high"], df["low"]
        temp = calc_temperature(close, high, low)
        pos = simulate_symbol_positions(close, high, low, temp)
        t_val = float(temp.iloc[-1])
        state = bucket_temperature_series(temp).iloc[-1]
        rows.append({
            "symbol": symbol,
            "name": desc,
            "T": round(t_val, 1),
            "state": state,
            "size": float(pos["size"].iloc[-1]),
            "date": str(df["date"].iloc[-1].date()),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("T", na_position="last", ascending=False)

    print("\n" + "=" * 64)
    print("  市场温度一览（冻-寒-凉-平-温-热-沸）")
    print("=" * 64)
    print(f"{'代码':<10}{'名称':<16}{'温度':>6}  {'状态':<4}{'热度':>6}  日期")
    print("-" * 64)
    for _, r in df.iterrows():
        t_str = f"{r['T']:.1f}" if r["T"] is not None else "—"
        print(f"{r['symbol']:<10}{r['name']:<16}{t_str:>6}  {r['state']:<4}{r['size']:>6.2f}  {r['date']}")
    print("-" * 64)

    counts = df["state"].value_counts()
    summary = "  ".join(f"{s}:{counts.get(s, 0)}" for s in TEMP_STATES)
    print(f"分布: {summary}")
    print()


if __name__ == "__main__":
    main()
