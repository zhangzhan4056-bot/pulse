"""Streamlit Cloud 入口兼容层。

Cloud 应用的 Main file path 仍是 app.py；主页逻辑在 0_主页.py。
app/ 是前端组件包，Python 中包优先于同名模块，`from app.components...` 不受影响。
"""
from pathlib import Path

_home = Path(__file__).with_name("0_主页.py")
exec(compile(_home.read_text(encoding="utf-8"), str(_home), "exec"))
