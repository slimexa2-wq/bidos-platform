import math

class CompetitorWinPriceAnomalyRadar:
    """
    Analyzes historical competitor win prices and discount rates.
    Implements a zero-configuration fault tolerance and automatic data healing.
    Uses Z-score to scan for anomalies and flags the top 3% with Z > 1.0 as 'red'.
    Healed data is flagged as 'yellow', and normal data as 'green'.
    """

    def __init__(self):
        self.audit_log = []

    def analyze(self, raw_data):
        """
        Analyzes the raw data array.

        :param raw_data: List of dicts with 'competitor', 'budget', 'win_price'.
        :return: List of processed dicts with additional 'discount_rate', 'z_score', 'color', 'reason' keys.
        """
        self.audit_log = []

        if not isinstance(raw_data, list):
            self.audit_log.append(f"Input is not a list, got {type(raw_data).__name__}. Defaulting to empty list.")
            raw_data = []

        processed_data = []
        rates = []

        for idx, item in enumerate(raw_data):
            is_healed = False

            if not isinstance(item, dict):
                self.audit_log.append(f"Item {idx} is not a dict. Reconstructing.")
                is_healed = True
                item = {}

            competitor = item.get('competitor', 'Unknown')
            if not isinstance(competitor, str):
                self.audit_log.append(f"Item {idx} competitor is not string. Converted to string.")
                competitor = str(competitor)
                is_healed = True

            budget = item.get('budget')
            if budget is None or not isinstance(budget, (int, float)) or budget <= 0:
                self.audit_log.append(f"Item {idx} invalid budget: {budget}. Defaulting to 10000.0.")
                is_healed = True
                budget = 10000.0

            win_price = item.get('win_price')
            if win_price is None or not isinstance(win_price, (int, float)) or win_price < 0:
                self.audit_log.append(f"Item {idx} invalid win_price: {win_price}. Defaulting to {float(budget)}.")
                is_healed = True
                win_price = float(budget)

            budget = float(budget)
            win_price = float(win_price)
            discount_rate = win_price / budget

            processed_item = {
                'competitor': competitor,
                'budget': budget,
                'win_price': win_price,
                'discount_rate': discount_rate,
                '_is_healed': is_healed,
                '_original_index': idx
            }
            processed_data.append(processed_item)
            rates.append(discount_rate)

        n = len(rates)
        if n == 0:
            return []

        mean_rate = sum(rates) / n
        if n > 1:
            variance = sum((r - mean_rate) ** 2 for r in rates) / n
            std_dev = math.sqrt(variance)
            if std_dev == 0:
                std_dev = 1e-9
        else:
            std_dev = 1e-9

        for item in processed_data:
            item['z_score'] = abs(item['discount_rate'] - mean_rate) / std_dev

        # Determine Top 3% threshold
        sorted_indices = sorted(range(n), key=lambda i: (-processed_data[i]['z_score'], processed_data[i]['_original_index']))
        top_3_count = math.ceil(n * 0.03)

        red_count = 0
        for i in sorted_indices:
            item = processed_data[i]
            if red_count < top_3_count and item['z_score'] > 1.0:
                item['color'] = 'red'
                item['reason'] = f"Top 3% anomalous discount rate (Z-score: {item['z_score']:.2f} > 1.0)"
                red_count += 1
            elif item['_is_healed']:
                item['color'] = 'yellow'
                item['reason'] = "Data auto-healed and reconstructed"
            else:
                item['color'] = 'green'
                item['reason'] = "Normal"

        # Cleanup internal keys and restore original order
        final_results = [None] * n
        for item in processed_data:
            idx = item.pop('_original_index')
            item.pop('_is_healed')
            final_results[idx] = item

        return final_results
