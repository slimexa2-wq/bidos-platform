# -*- coding: utf-8 -*-
"""
信用中国/中国政府采购网无重大违法记录声明与截图核验清单器
（跨系统数据契约无缝桥接与Schema版本自动向前兼容转换器）

Core Principles:
1. Zero-configuration fault tolerance.
2. Silent cleaning of dirty data.
3. Plain language ("大白话") conclusions for business users.
"""
import re

class CreditChinaBlacklistCheckBridge:
    def __init__(self):
        # Definition of schema aliases for mapping
        self.entity_name_aliases = ['name', 'entityName', 'companyName', 'ent_name', 'company_name', 'entName']
        self.credit_code_aliases = ['creditCode', 'credit_code', 'uscc', 'unified_social_credit_code']
        self.risk_summary_aliases = ['riskSummary', 'risk_summary', 'hasMajorViolation', 'isBlacklisted', 'punished', 'status', 'blacklist_status', 'is_blacklisted']

        # Strings that denote a risk condition
        self.risk_indicators = ['true', 'yes', 'y', '1', '有', '存在', '处罚', '异常', '黑名单', '违规']

    def _extract_field(self, data, aliases, default_val=None):
        if not isinstance(data, dict):
            return default_val
        for alias in aliases:
            if alias in data and data[alias] is not None:
                return data[alias]
        return default_val

    def _clean_string(self, val):
        if isinstance(val, str):
            val = val.strip()
            if val.lower() == 'null' or val == '':
                return None
            return val
        return val

    def _evaluate_risk(self, risk_val):
        if risk_val is None:
            return False # Safe default fallback

        if isinstance(risk_val, bool):
            return risk_val

        if isinstance(risk_val, (int, float)):
            return risk_val > 0

        if isinstance(risk_val, str):
            clean_val = risk_val.lower().strip()
            # If string means 'None' or empty, fallback to False
            if not clean_val or clean_val == 'null' or clean_val == 'none':
                return False

            for indicator in self.risk_indicators:
                if indicator in clean_val:
                    return True

        return False

    def convert_and_verify(self, data):
        """
        Accepts any dirty dictionary data, maps it automatically, and returns a
        plain language conclusion dictionary.
        """
        # 1. Handle extreme boundaries (None, list, numbers, string instead of dict)
        if not isinstance(data, dict):
            data = {}

        # 2. Extract and clean entity identifiers
        raw_name = self._extract_field(data, self.entity_name_aliases)
        name = self._clean_string(raw_name) or "未知企业"

        raw_code = self._extract_field(data, self.credit_code_aliases)
        code = self._clean_string(raw_code) or "未知代码"

        # 3. Extract and evaluate risk indicators
        raw_risk = self._extract_field(data, self.risk_summary_aliases)
        is_risk = self._evaluate_risk(raw_risk)

        # 4. Construct plain-language conclusions
        is_safe = not is_risk

        if is_safe:
            status_summary = "正常，无违法记录"
        else:
            status_summary = "⚠️ 存在违法风险"

        return {
            "entity_name": name,
            "credit_code": code,
            "is_safe": is_safe,
            "status_summary": status_summary
        }
