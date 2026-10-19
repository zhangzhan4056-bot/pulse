"""飞书 Webhook 推送 — 告警通知"""

import json
import logging
import os
from typing import Dict, List

import requests

logger = logging.getLogger(__name__)


class FeishuNotifier:
    """飞书自定义机器人 Webhook 推送"""

    def __init__(self):
        self.webhook_url = os.environ.get("FEISHU_WEBHOOK_URL", "")

    def is_configured(self) -> bool:
        """是否已配置 Webhook URL"""
        return bool(self.webhook_url)

    def _post(self, payload: dict) -> bool:
        """发送请求到飞书 Webhook"""
        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("code") == 0 or result.get("StatusCode") == 0:
                    return True
                logger.warning(f"飞书推送返回错误: {result}")
                return False
            logger.warning(f"飞书推送 HTTP {resp.status_code}")
            return False
        except Exception as e:
            logger.error(f"飞书推送失败: {e}")
            return False

    def send(self, title: str, message: str, severity: str = "info") -> bool:
        """发送简单飞书卡片消息

        Args:
            title: 消息标题
            message: 消息内容（支持 Markdown）
            severity: 严重程度 (info/warning/critical)

        Returns:
            是否发送成功
        """
        if not self.webhook_url:
            logger.debug("飞书 Webhook 未配置，跳过推送")
            return False

        color_map = {"info": "blue", "warning": "orange", "critical": "red"}
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color_map.get(severity, "blue"),
                },
                "elements": [{"tag": "markdown", "content": message}],
            },
        }
        return self._post(payload)

    def send_card(self, title: str, elements: List[Dict],
                  severity: str = "info") -> bool:
        """发送结构化飞书卡片消息（多段内容 + 折叠 + 分割线）

        Args:
            title: 卡片标题
            elements: 飞书卡片 elements 数组，每项是 dict：
                - {"tag": "markdown", "content": "..."}
                - {"tag": "hr"}
                - {"tag": "collapsible", "expanded": False,
                   "title": {"tag": "plain_text", "content": "..."},
                   "elements": [...]}
            severity: 严重程度 (info/warning/critical)

        Returns:
            是否发送成功
        """
        if not self.webhook_url:
            logger.debug("飞书 Webhook 未配置，跳过推送")
            return False

        color_map = {"info": "blue", "warning": "orange", "critical": "red"}
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color_map.get(severity, "blue"),
                },
                "elements": elements,
            },
        }
        return self._post(payload)

    def send_alert_batch(self, alerts: List[Dict]) -> int:
        """批量发送告警（合并为一条消息避免刷屏）

        Args:
            alerts: 告警字典列表

        Returns:
            成功发送的告警数
        """
        if not alerts:
            return 0

        if not self.is_configured():
            logger.debug("飞书 Webhook 未配置，跳过推送")
            return 0

        # 按严重程度分组
        critical = [a for a in alerts if a.get("severity") == "critical"]
        warning = [a for a in alerts if a.get("severity") == "warning"]
        info = [a for a in alerts if a.get("severity") == "info"]

        # 合并为一条消息
        parts = []
        if critical:
            parts.append("**  严重告警**")
            for a in critical:
                parts.append(f"- {a['title']}: {a['message'][:100]}")
        if warning:
            parts.append("**  警告**")
            for a in warning:
                parts.append(f"- {a['title']}: {a['message'][:100]}")
        if info:
            parts.append("**  提醒**")
            for a in info:
                parts.append(f"- {a['title']}")

        message = "\n".join(parts)

        # 确定最高严重程度
        max_severity = "info"
        if critical:
            max_severity = "critical"
        elif warning:
            max_severity = "warning"

        title = f"Market Pulse 风险预警 ({len(alerts)} 条)"
        success = self.send(title, message, max_severity)

        return len(alerts) if success else 0
