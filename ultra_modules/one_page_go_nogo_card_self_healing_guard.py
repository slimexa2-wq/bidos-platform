import json
import uuid
import time
from typing import Dict, Any, Optional

class OnePageGoNoGoCardSelfHealingGuard:
    """
    一页式极简投标决策卡模块
    包含异常输入静默自愈与5秒一键撤回(Undo)快照机制。
    """

    def __init__(self):
        # 存储快照 {snapshot_id: {"timestamp": int, "data": dict}}
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        # 撤回时间窗口（秒）
        self.undo_window = 5

    def _heal_input(self, rfp_content: Any) -> str:
        """
        异常输入静默自愈。将各种脏数据转换为合理的字符串进行后续处理。
        """
        if rfp_content is None:
            return ""
        if isinstance(rfp_content, str):
            return rfp_content.strip()
        if isinstance(rfp_content, dict):
            # 将 dict 转为字符串以提取特征词
            return json.dumps(rfp_content, ensure_ascii=False)
        if isinstance(rfp_content, list):
            # 将 list 转换为以空格分隔的字符串
            return " ".join([self._heal_input(item) for item in rfp_content])

        # 处理其他未知的可 stringify 类型
        try:
            return str(rfp_content).strip()
        except Exception:
            return ""

    def evaluate(self, rfp_content: Any = None, **kwargs) -> Dict[str, Any]:
        """
        1秒生成【可投/慎投/禁投】一页式极简投标决策卡
        """
        # 静默自愈处理输入数据
        clean_content = self._heal_input(rfp_content)

        # 如果 clean_content 为空或者缺参，使用合理默认值
        # 缺参自动设合理默认值，静默处理
        if not clean_content:
            decision = "慎投"
            reason = "缺少有效的招标书内容，建议进一步评估"
        else:
            # 简单的关键词匹配逻辑来决定结果
            # 真实生产级应用会有更复杂的 NLP 或正则模型，这里为了核心逻辑直接实现
            forbidden_keywords = ["黑名单", "失信", "暂停投标", "不接受联合体", "处罚期"]
            caution_keywords = ["垫资", "偏远", "定制开发", "极短工期", "要求过高"]

            if any(keyword in clean_content for keyword in forbidden_keywords):
                decision = "禁投"
                reason = "触发禁投条款或高危关键词"
            elif any(keyword in clean_content for keyword in caution_keywords):
                decision = "慎投"
                reason = "包含需谨慎评估的条款或要求"
            else:
                decision = "可投"
                reason = "未发现明显风险，符合常规投标要求"

        # 生成快照 ID 以支持 5 秒一键撤回
        snapshot_id = str(uuid.uuid4())
        timestamp = int(time.time())

        # 构造决策卡数据
        card = {
            "snapshot_id": snapshot_id,
            "decision": decision,
            "reason": reason,
            "timestamp": timestamp,
            "processed_length": len(clean_content)
        }

        # 保存快照
        self._snapshots[snapshot_id] = {
            "timestamp": timestamp,
            "data": card.copy()
        }

        return card

    def undo(self, snapshot_id: str) -> Dict[str, Any]:
        """
        5秒一键撤回(Undo)快照机制
        若在时间窗口内，则返回撤回成功；否则静默返回失败状态（不弹窗报错）。
        """
        # 静默容错处理
        if not isinstance(snapshot_id, str):
            snapshot_id = str(snapshot_id) if snapshot_id is not None else ""

        if snapshot_id not in self._snapshots:
            return {
                "success": False,
                "message": "快照不存在或已失效",
                "snapshot_id": snapshot_id
            }

        snapshot = self._snapshots[snapshot_id]
        current_time = int(time.time())

        if current_time - snapshot["timestamp"] <= self.undo_window:
            # 撤回成功，清理快照
            del self._snapshots[snapshot_id]
            return {
                "success": True,
                "message": "撤回成功",
                "snapshot_id": snapshot_id,
                "restored_data": snapshot["data"]
            }
        else:
            # 超时撤回失败，不报错，温和返回
            return {
                "success": False,
                "message": "已超过5秒撤回窗口，撤回失败",
                "snapshot_id": snapshot_id
            }
