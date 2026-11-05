import datetime
import json
import re
from typing import Dict, Any, Optional, Union

class QualificationExpiryCheckerPlainExplainer:
    """
    Qualification Expiry Checker Plain Explainer
    Checks the expiry date of qualifications and generates plain-language, simple outputs.
    """
    def __init__(self, warning_days: int = 30):
        # Silently handle dirty/missing warning_days
        try:
            self.warning_days = int(warning_days)
            if self.warning_days < 0:
                self.warning_days = 30
        except (ValueError, TypeError):
            self.warning_days = 30

    def _is_long_term(self, date_val: Any) -> bool:
        if isinstance(date_val, str):
            keywords = ["长期", "无期", "永久", "不限", "9999"]
            if any(kw in date_val for kw in keywords):
                return True
        return False

    def _parse_date(self, date_val: Union[str, datetime.datetime, datetime.date, None]) -> Optional[datetime.date]:
        """Robustly parse date to a datetime.date object. Handles dirty data silently."""
        if not date_val:
            return None

        if isinstance(date_val, datetime.datetime):
            return date_val.date()
        if isinstance(date_val, datetime.date):
            return date_val

        if isinstance(date_val, str):
            # Attempt to clean the string
            clean_str = re.sub(r'[^\d\-\./]', '', date_val)

            # Common formats
            formats = [
                "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d",
                "%Y%m%d"
            ]
            for fmt in formats:
                try:
                    return datetime.datetime.strptime(clean_str, fmt).date()
                except ValueError:
                    continue

            # Less precise formats (e.g. 2024-10 means 2024-10-01)
            less_precise_formats = ["%Y-%m", "%Y/%m", "%Y.%m"]
            for fmt in less_precise_formats:
                try:
                    return datetime.datetime.strptime(clean_str, fmt).date()
                except ValueError:
                    continue

        return None

    def check_expiry(self,
                     qualification_name: str,
                     expiry_date: Union[str, datetime.datetime, datetime.date, None],
                     reference_date: Optional[Union[str, datetime.datetime, datetime.date]] = None) -> Dict[str, Any]:
        """
        Check the expiry and generate explanations.
        """
        # Default name if missing or empty
        q_name = str(qualification_name).strip() if qualification_name else "资质证书"
        if not q_name or q_name == 'None':
            q_name = "资质证书"

        parsed_ref = self._parse_date(reference_date)
        if not parsed_ref:
            parsed_ref = datetime.date.today()

        if self._is_long_term(expiry_date):
            status = "safe"
            remaining_days = 99999
            message = f"放心，【{q_name}】是长期的，安心使用！"
            color = "#52c41a" # Green
            expiry_str = "长期有效"
        else:
            parsed_expiry = self._parse_date(expiry_date)

            if not parsed_expiry:
                status = "unknown"
                remaining_days = None
                message = f"【{q_name}】的有效期无法识别，请手动核对哦！"
                color = "#808080" # Gray
                expiry_str = "未知"
            else:
                expiry_str = parsed_expiry.strftime("%Y-%m-%d")
                remaining_days = (parsed_expiry - parsed_ref).days

                if remaining_days < 0:
                    status = "expired"
                    message = f"注意！【{q_name}】已经过期了 {abs(remaining_days)} 天，请立刻处理！"
                    color = "#ff4d4f" # Red
                elif remaining_days == 0:
                    status = "expired"
                    message = f"注意！【{q_name}】今天到期，请立刻处理！"
                    color = "#ff4d4f" # Red
                elif remaining_days <= self.warning_days:
                    status = "warning"
                    message = f"提醒：【{q_name}】还有 {remaining_days} 天就要到期了，可以开始准备续期啦。"
                    color = "#faad14" # Yellow/Orange
                else:
                    status = "safe"
                    message = f"放心，【{q_name}】还在有效期内（还有 {remaining_days} 天），安心使用！"
                    color = "#52c41a" # Green

        # Prepare outputs
        json_output = {
            "qualification_name": q_name,
            "expiry_date": expiry_str,
            "status": status,
            "remaining_days": remaining_days,
            "plain_message": message
        }

        html_output = f'''
        <div class="qualification-card" style="border: 1px solid #e8e8e8; border-radius: 8px; padding: 16px; max-width: 400px; font-family: sans-serif; box-shadow: 0 2px 8px rgba(0,0,0,0.05);">
            <div style="display: flex; align-items: center; margin-bottom: 12px;">
                <div style="width: 12px; height: 12px; border-radius: 50%; background-color: {color}; margin-right: 8px;"></div>
                <strong style="font-size: 16px; color: #333;">{q_name}</strong>
            </div>
            <p style="margin: 0 0 8px 0; font-size: 14px; color: #666;">
                到期日: <span style="font-weight: 500; color: #333;">{expiry_str}</span>
            </p>
            <div style="background-color: #f9f9f9; padding: 12px; border-radius: 6px; font-size: 14px; color: {color}; font-weight: 500;">
                {message}
            </div>
        </div>
        '''

        markdown_output = f"**{q_name}** (到期日: {expiry_str})\n> {message}"

        return {
            "status": status,
            "remaining_days": remaining_days,
            "message": message,
            "json": json.dumps(json_output, ensure_ascii=False),
            "html": html_output.strip(),
            "markdown": markdown_output
        }
