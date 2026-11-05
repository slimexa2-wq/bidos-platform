import re
import math
import statistics

class RFPAnomalyRadar:
    def __init__(self):
        # Disqualification: ★, Deduction: ▲
        self.disqual_pattern = re.compile(r'★(.*)')
        self.deduct_pattern = re.compile(r'▲(.*)')
        self.anomaly_keywords = ['未提供', '不符合', '废标', '弄虚作假', '拒绝', '不响应']

    def _extract_items(self, text):
        if not isinstance(text, str):
            return [], []
        disquals = self.disqual_pattern.findall(text)
        deducts = self.deduct_pattern.findall(text)
        return [d.strip() for d in disquals if d.strip()], [d.strip() for d in deducts if d.strip()]

    def _compute_semantic_score(self, text):
        score = len(text) * 0.1
        for kw in self.anomaly_keywords:
            if kw in text:
                score += 10
        return score

    def _z_score_analysis(self, items):
        if not items:
            return []

        scores = [self._compute_semantic_score(item) for item in items]
        n = len(scores)

        if n < 2:
            return [{'text': items[0], 'score': scores[0], 'z_score': 0.0, 'is_anomaly': False}]

        mean = statistics.mean(scores)
        try:
            stdev = statistics.stdev(scores)
        except statistics.StatisticsError:
            stdev = 0.0

        result = []
        for i in range(n):
            item = items[i]
            score = scores[i]

            if stdev == 0:
                z = 0.0
            else:
                z = (score - mean) / stdev

            result.append({'text': item, 'score': score, 'z_score': z})

        # Top 3% anomalous
        top_k = max(1, math.ceil(n * 0.03))

        # Sort by z_score descending
        sorted_indices = sorted(range(n), key=lambda x: result[x]['z_score'], reverse=True)
        anomaly_indices = set(sorted_indices[:top_k])

        for i, res in enumerate(result):
            # To strictly follow "Top 3% anomalous", we mark exactly those indices if they have some variance.
            # But if all items are identical (z_score=0), none are anomalous.
            if stdev == 0:
                 res['is_anomaly'] = False
                 res['color'] = 'GREEN'
                 res['reason'] = '正常'
            elif i in anomaly_indices:
                res['is_anomaly'] = True
                res['color'] = 'RED'
                res['reason'] = f'异常项：统计得分过高(Z-Score={res["z_score"]:.2f})，可能包含严苛条款'
            elif res["z_score"] > 0:
                res['is_anomaly'] = False
                res['color'] = 'YELLOW'
                res['reason'] = '提示项：得分略高于平均值'
            else:
                res['is_anomaly'] = False
                res['color'] = 'GREEN'
                res['reason'] = '正常'

        return result

    def process(self, rfp_text=None, **kwargs):
        """
        Main processing method.
        Missing params fallback: rfp_text defaults to ""
        Dirty data self-healing: Non-string inputs are converted or handled.
        """
        if rfp_text is None:
            rfp_text = ""
        elif isinstance(rfp_text, (list, tuple)):
            rfp_text = "\n".join([str(item) for item in rfp_text])
        elif not isinstance(rfp_text, str):
            try:
                rfp_text = str(rfp_text)
            except Exception:
                rfp_text = ""

        disquals, deducts = self._extract_items(rfp_text)

        all_items = disquals + deducts

        analyzed_items = self._z_score_analysis(all_items)

        return {
            'total_items': len(analyzed_items),
            'disqual_count': len(disquals),
            'deduct_count': len(deducts),
            'analyzed_items': analyzed_items
        }

def run_anomaly_radar(rfp_text=None, **kwargs):
    """
    Zero-configuration one-click function to run RFP anomaly radar.
    """
    radar = RFPAnomalyRadar()
    try:
        return radar.process(rfp_text, **kwargs)
    except Exception as e:
        # Dirty data self-healing, fallback return
        return {
            'total_items': 0,
            'disqual_count': 0,
            'deduct_count': 0,
            'analyzed_items': [],
            'error': str(e)
        }
