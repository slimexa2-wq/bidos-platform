import datetime
import re
from typing import Dict, Any, Optional

class BidBondReturnTrackerPlainExplainer:
    """
    投标保证金缴纳与退还跟踪大白话解释器
    """

    def __init__(self):
        pass

    def _clean_amount(self, amount_raw: Any) -> float:
        if amount_raw is None:
            return 0.0
        if isinstance(amount_raw, (int, float)):
            return float(amount_raw)

        # 脏数据自愈：处理字符串
        s = str(amount_raw).strip()
        if not s:
            return 0.0

        # 移除货币符号和逗号，但保留负号和小数点
        s = re.sub(r'[^\d\.-]', '', s)
        try:
            return float(s) if s else 0.0
        except ValueError:
            return 0.0

    def _parse_date(self, date_raw: Any, default_date: Optional[datetime.date] = None) -> Optional[datetime.date]:
        if date_raw is None:
            return default_date
        if isinstance(date_raw, datetime.date):
            return date_raw
        if isinstance(date_raw, datetime.datetime):
            return date_raw.date()

        s = str(date_raw).strip()
        if not s:
            return default_date

        # 脏数据自愈：处理常见日期字符串
        # 将非数字字符统一替换为连字符 -
        s = re.sub(r'[^\d]', '-', s)
        s = re.sub(r'-+', '-', s)
        try:
            # 处理 YYYYMMDD 格式（长度为8的纯数字）
            if len(s) == 8 and s.isdigit():
                return datetime.datetime.strptime(s, "%Y%m%d").date()

            # 处理 YYYY-MM-DD 等格式
            parts = s.split('-')
            if len(parts) >= 3:
                return datetime.date(int(parts[0]), int(parts[1]), int(parts[2]))
            return default_date
        except Exception:
            return default_date

    def analyze(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        参数:
        - project_name: 项目名称 (默认 '未知项目')
        - bond_amount: 保证金金额 (默认 0.0)
        - bid_opening_date: 开标日期 (默认 None)
        - return_deadline_days: 开标后最晚退还天数 (默认 5)
        - actual_return_date: 实际退还日期 (默认 None)
        - current_date: 当前日期 (用于计算是否逾期, 默认今天)
        """
        # 容错：缺省参数处理
        params = params or {}

        project_name = str(params.get('project_name') or '未知项目').strip()
        bond_amount = self._clean_amount(params.get('bond_amount'))

        today = datetime.date.today()
        current_date = self._parse_date(params.get('current_date'), today) or today

        bid_opening_date = self._parse_date(params.get('bid_opening_date'))
        actual_return_date = self._parse_date(params.get('actual_return_date'))

        try:
            return_deadline_days = int(params.get('return_deadline_days') or 5)
        except (ValueError, TypeError):
            return_deadline_days = 5

        # 核心逻辑
        deadline_date = None
        status = "未知"
        is_overdue = False
        overdue_days = 0

        if bid_opening_date:
            deadline_date = bid_opening_date + datetime.timedelta(days=return_deadline_days)

            if actual_return_date:
                status = "已退还"
                if actual_return_date > deadline_date:
                    is_overdue = True
                    overdue_days = (actual_return_date - deadline_date).days
            else:
                if current_date > deadline_date:
                    status = "逾期未退还"
                    is_overdue = True
                    overdue_days = (current_date - deadline_date).days
                else:
                    status = "待退还（未逾期）"
        else:
            if actual_return_date:
                status = "已退还"
            else:
                status = "等待开标"

        # 生成大白话
        plain_text = f"项目【{project_name}】的投标保证金为 {bond_amount:,.2f} 元。"
        if status == "等待开标":
            plain_text += " 目前尚未开标，暂无退还期限。"
        elif status == "待退还（未逾期）":
            plain_text += f" 已于 {bid_opening_date} 开标，最晚应在 {deadline_date} 前退还，目前处于正常等待期。"
        elif status == "逾期未退还":
            plain_text += f" 已于 {bid_opening_date} 开标，本该在 {deadline_date} 前退还，但至今未退！已经逾期 {overdue_days} 天，建议立刻催办！"
        elif status == "已退还":
            if is_overdue:
                plain_text += f" 虽然已在 {actual_return_date} 退还，但比规定最晚时间（{deadline_date}）晚了 {overdue_days} 天。"
            else:
                if deadline_date:
                    plain_text += f" 已经在 {actual_return_date} 顺利退还，符合 {deadline_date} 前退还的要求。"
                else:
                    plain_text += f" 已经在 {actual_return_date} 退还。"

        # 组装 JSON 结果
        json_data = {
            "project_name": project_name,
            "bond_amount": bond_amount,
            "bid_opening_date": str(bid_opening_date) if bid_opening_date else None,
            "deadline_date": str(deadline_date) if deadline_date else None,
            "actual_return_date": str(actual_return_date) if actual_return_date else None,
            "status": status,
            "is_overdue": is_overdue,
            "overdue_days": overdue_days,
            "plain_text": plain_text
        }

        # 组装 Markdown 卡片
        status_color = "🔴" if is_overdue and not actual_return_date else "🟢" if actual_return_date else "🟡"
        markdown_card = f"""### 投标保证金台账卡 📝
**项目名称:** {project_name}
**保证金额:** ￥{bond_amount:,.2f}
**状态:** {status_color} {status}

- **开标日期:** {bid_opening_date or '暂无'}
- **应退截止:** {deadline_date or '暂无'}
- **实际退还:** {actual_return_date or '暂无'}
{f'- **逾期天数:** {overdue_days}天 ⚠️' if is_overdue else ''}

💡 **大白话结论:**
> {plain_text}
"""

        # 组装 HTML 卡片
        html_color = "#dc3545" if is_overdue and not actual_return_date else "#28a745" if actual_return_date else "#ffc107"
        text_color = "#fff" if html_color != "#ffc107" else "#333"
        html_card = f"""<div style="border: 1px solid #ddd; border-radius: 8px; padding: 16px; max-width: 400px; font-family: sans-serif; box-shadow: 0 4px 6px rgba(0,0,0,0.1);">
    <h3 style="margin-top: 0; color: #333;">投标保证金台账卡 📝</h3>
    <div style="margin-bottom: 12px;"><strong>项目:</strong> {project_name}</div>
    <div style="margin-bottom: 12px;"><strong>金额:</strong> ￥{bond_amount:,.2f}</div>
    <div style="display: inline-block; padding: 4px 8px; border-radius: 4px; background-color: {html_color}; color: {text_color}; font-weight: bold; margin-bottom: 12px;">
        {status}
    </div>
    <ul style="list-style-type: none; padding-left: 0; margin-bottom: 16px; color: #555;">
        <li>开标日期: {bid_opening_date or '暂无'}</li>
        <li>应退截止: {deadline_date or '暂无'}</li>
        <li>实际退还: {actual_return_date or '暂无'}</li>
        {f'<li style="color: #dc3545; font-weight: bold;">逾期天数: {overdue_days}天 ⚠️</li>' if is_overdue else ''}
    </ul>
    <div style="background-color: #f8f9fa; padding: 12px; border-left: 4px solid #007bff; border-radius: 4px; color: #333; font-size: 0.95em;">
        <strong>💡 大白话结论:</strong><br/>
        {plain_text}
    </div>
</div>"""

        return {
            "json_data": json_data,
            "markdown_card": markdown_card.strip(),
            "html_card": html_card.strip()
        }
