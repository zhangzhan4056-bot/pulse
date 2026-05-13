#!/usr/bin/env python3
"""数据层验证脚本

测试数据获取、存储、读取流程。

用法:
    export TWELVEDATA_API_KEY="your_key"
    python scripts/test_data.py
"""

import sys
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.data import DataManager


def main():
    print("=" * 60)
    print("Market Pulse 数据层验证")
    print("=" * 60)

    dm = DataManager()

    # 测试 1: Twelve Data API
    print("\n[1] 测试 Twelve Data API...")
    try:
        quote = dm.get_quote("SPY")
        print(f"  ✓ SPY 最新价: ${quote['close']}")
        print(f"  ✓ 涨跌幅: {quote['percent_change']}%")
    except Exception as e:
        print(f"  ✗ Twelve Data API 失败: {e}")
        return

    # 测试 2: 获取美股历史数据
    print("\n[2] 获取美股大类资产历史数据...")
    results = dm.fetch_us_assets(outputsize=10)
    for symbol, count in results.items():
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {symbol}: {count} 条")

    # 测试 3: AkShare
    print("\n[3] 测试 AkShare A 股数据...")
    try:
        df = dm.get_cn_realtime()
        if not df.empty:
            print(f"  ✓ 获取 A 股实时行情成功，{len(df)} 条")
            # 显示前 3 条
            for _, row in df.head(3).iterrows():
                print(f"    {row['name']}: {row['price']} ({row['change_pct']}%)")
        else:
            print("  ✗ A 股实时行情为空")
    except Exception as e:
        print(f"  ✗ AkShare 失败: {e}")

    # 测试 4: 获取 A 股历史数据
    print("\n[4] 获取 A 股指数历史数据...")
    cn_results = dm.fetch_cn_assets()
    for symbol, count in cn_results.items():
        status = "✓" if count > 0 else "✗"
        print(f"  {status} {symbol}: {count} 条")

    # 测试 5: 从数据库读取
    print("\n[5] 测试数据读取...")
    df = dm.load("SPY")
    if not df.empty:
        print(f"  ✓ 从 SQLite 读取 SPY 数据: {len(df)} 条")
        print(f"  ✓ 日期范围: {df['date'].min().date()} ~ {df['date'].max().date()}")
    else:
        print("  ✗ SPY 数据为空")

    # 测试 6: 数据库统计
    print("\n[6] 数据库统计:")
    stats = dm.get_stats()
    if not stats.empty:
        for _, row in stats.iterrows():
            print(f"  {row['symbol']}: {row['rows']} 条 ({row['first_date']} ~ {row['last_date']})")
    else:
        print("  数据库为空")

    print("\n" + "=" * 60)
    print("验证完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
