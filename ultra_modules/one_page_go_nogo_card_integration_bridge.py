import json
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class SchemaConverter:
    """
    Handles schema versions and backward compatibility mapping.
    Silently resolves differences and maps legacy fields to new formats.
    """

    @staticmethod
    def convert(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts any recognized data format into the canonical V2 format.
        Tolerates missing fields and dirty data.
        """
        if not isinstance(data, dict):
            return {}

        result = {}

        # Safe extraction with reasonable defaults
        result['project_name'] = str(data.get('project_name', data.get('projectName', data.get('title', 'Unknown Project')))).strip()
        result['budget'] = SchemaConverter._parse_float(data.get('budget', data.get('estimatedAmount', data.get('price', 0.0))))

        # Risk factors (0.0 to 1.0)
        risk_raw = data.get('risk_level', data.get('risk', 'low'))
        result['risk_score'] = SchemaConverter._map_risk(risk_raw)

        # Capability match (0.0 to 1.0)
        match_raw = data.get('capability_match', data.get('matchScore', data.get('fit', 0.5)))
        result['capability_score'] = SchemaConverter._parse_float(match_raw, default=0.5)

        return result

    @staticmethod
    def _parse_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or str(value).strip() == '':
                return default
            # Handle currency strings or weird characters
            clean_val = ''.join(c for c in str(value) if c.isdigit() or c in ['.', '-'])
            return float(clean_val) if clean_val else default
        except Exception:
            return default

    @staticmethod
    def _map_risk(risk_val: Any) -> float:
        if isinstance(risk_val, (int, float)):
            return float(max(0.0, min(1.0, risk_val)))

        risk_str = str(risk_val).lower()
        if 'high' in risk_str or 'severe' in risk_str or '禁' in risk_str:
            return 0.9
        if 'medium' in risk_str or 'warn' in risk_str or '慎' in risk_str:
            return 0.5
        if 'low' in risk_str or 'safe' in risk_str or '可' in risk_str:
            return 0.1

        return 0.5 # Default fallback

class DecisionEngine:
    """
    Calculates final recommendation (Go / No-Go / Caution) based on scores.
    """

    @staticmethod
    def evaluate(standardized_data: Dict[str, Any]) -> Dict[str, Any]:
        capability = standardized_data.get('capability_score', 0.0)
        risk = standardized_data.get('risk_score', 0.5)

        # Simple algorithm for demonstration
        score = (capability * 100) - (risk * 50)

        if risk > 0.8 or score < 20:
            decision = '禁投'  # No-Go
            reason = '风险过高或匹配度极低'
        elif risk > 0.4 or score < 60:
            decision = '慎投'  # Caution
            reason = '存在一定风险，需要进一步评估'
        else:
            decision = '可投'  # Go
            reason = '匹配度高且风险可控'

        return {
            'decision': decision,
            'reason': reason,
            'score': score,
            'summary': f"项目【{standardized_data.get('project_name')}】判定结果：{decision}。{reason}"
        }

class IntegrationBridge:
    """
    Main entry point for external calls.
    Follows zero-configuration and auto-fallback rules.
    """

    @classmethod
    def generate_card(cls, raw_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        One-click generation of the Go/No-Go card.
        Tolerates completely empty or dirty input.
        """
        try:
            if raw_data is None:
                raw_data = {}

            standardized = SchemaConverter.convert(raw_data)
            evaluation = DecisionEngine.evaluate(standardized)

            return {
                'success': True,
                'data': {
                    'project_name': standardized.get('project_name'),
                    'budget': standardized.get('budget'),
                    'decision': evaluation['decision'],
                    'reason': evaluation['reason'],
                    'score': evaluation['score'],
                    'card_view': evaluation['summary']
                }
            }
        except Exception as e:
            logger.error(f"IntegrationBridge failed, returning fallback card. Error: {e}")
            # Extreme fallback
            return {
                'success': False,
                'data': {
                    'project_name': 'Unknown',
                    'budget': 0.0,
                    'decision': '慎投',
                    'reason': '系统异常，已启用安全回退策略',
                    'score': 0,
                    'card_view': '系统异常，请人工介入评估'
                }
            }
