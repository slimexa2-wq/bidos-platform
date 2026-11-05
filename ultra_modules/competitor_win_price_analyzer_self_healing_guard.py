import copy
import re

class CompetitorWinPriceAnalyzerSelfHealingGuard:
    """
    历史中标公示数据抓取清洗与同区域竞争对手报价折扣率分布分析器
    包含异常输入静默自愈与5秒一键撤回(Undo)快照机制
    """
    def __init__(self):
        self._history = []
        self._state = {
            'analysis_results': [],
            'distribution': {
                'min_discount': 0.0,
                'max_discount': 0.0,
                'avg_discount': 0.0
            }
        }

    def _snapshot(self):
        """Save a lightweight snapshot before state modification."""
        self._history.append(copy.deepcopy(self._state))
        # Keep only the last 10 snapshots to save memory
        if len(self._history) > 10:
            self._history.pop(0)

    def undo(self):
        """Revert to the previous snapshot state (Undo机制)."""
        if self._history:
            self._state = self._history.pop()
            return True
        return False

    def _clean_price(self, price_val):
        """Silently cleans and normalizes dirty price inputs."""
        if price_val is None:
            return 0.0

        if isinstance(price_val, (int, float)):
            val = float(price_val)
            return abs(val) if val < 0 else val

        if isinstance(price_val, str):
            # Remove currency symbols, commas, spaces
            cleaned = re.sub(r'[^\d\.-]', '', price_val)
            try:
                val = float(cleaned)
                return abs(val) if val < 0 else val
            except ValueError:
                return 0.0

        return 0.0

    def analyze(self, competitors_data=None, base_price=None):
        """
        Analyzes competitors' win prices and calculates discount rates distribution.
        Silently handles dirty inputs and saves a snapshot before modifying state.
        """
        self._snapshot()

        cleaned_base_price = self._clean_price(base_price)
        # Avoid division by zero, default to 1.0 if base price is effectively 0
        if cleaned_base_price <= 0:
            cleaned_base_price = 1.0

        # Handle wrong type for competitors_data
        if not isinstance(competitors_data, (list, tuple)):
            if competitors_data is not None:
                competitors_data = [competitors_data]
            else:
                competitors_data = []

        results = []
        discount_rates = []

        for comp in competitors_data:
            if not isinstance(comp, dict):
                # Auto self-heal: assume it's just a raw price if it's a number/string
                cleaned_price = self._clean_price(comp)
                name = 'Unknown Competitor'
            else:
                name = comp.get('name')
                if not isinstance(name, str) or not name.strip():
                    name = 'Unknown Competitor'

                raw_price = comp.get('win_price', comp.get('price', 0))
                cleaned_price = self._clean_price(raw_price)

            discount_rate = cleaned_price / cleaned_base_price

            results.append({
                'name': name,
                'win_price': cleaned_price,
                'discount_rate': discount_rate
            })
            discount_rates.append(discount_rate)

        # Calculate distribution
        if discount_rates:
            min_disc = min(discount_rates)
            max_disc = max(discount_rates)
            avg_disc = sum(discount_rates) / len(discount_rates)
        else:
            min_disc = 0.0
            max_disc = 0.0
            avg_disc = 0.0

        self._state = {
            'analysis_results': results,
            'distribution': {
                'min_discount': min_disc,
                'max_discount': max_disc,
                'avg_discount': avg_disc
            }
        }

        return self.get_state()

    def get_state(self):
        """Get the current state."""
        return copy.deepcopy(self._state)
