import unittest
from ultra_modules.contract_90day_expiry_radar_anomaly_radar import scan_anomalies

class TestContractAnomalyRadar(unittest.TestCase):
    def test_empty_list(self):
        """Test with an empty list."""
        self.assertEqual(scan_anomalies([]), [])

    def test_normal_dataset(self):
        """Test with normal dataset of 10 items, checking top 3% (which is ceil(10*0.03)=1 item)."""
        contracts = [
            {'id': 1, 'days_to_expiry': 120, 'bond_delay_days': 0, 'bond_amount': 10000},
            {'id': 2, 'days_to_expiry': 200, 'bond_delay_days': 0, 'bond_amount': 20000},
            {'id': 3, 'days_to_expiry': 15, 'bond_delay_days': 5, 'bond_amount': 50000}, # The riskiest one
            {'id': 4, 'days_to_expiry': 300, 'bond_delay_days': 0, 'bond_amount': 5000},
            {'id': 5, 'days_to_expiry': 400, 'bond_delay_days': 0, 'bond_amount': 8000},
            {'id': 6, 'days_to_expiry': 110, 'bond_delay_days': 0, 'bond_amount': 0},
            {'id': 7, 'days_to_expiry': 105, 'bond_delay_days': 0, 'bond_amount': 1000},
            {'id': 8, 'days_to_expiry': 130, 'bond_delay_days': 0, 'bond_amount': 2000},
            {'id': 9, 'days_to_expiry': 140, 'bond_delay_days': 0, 'bond_amount': 3000},
            {'id': 10, 'days_to_expiry': 150, 'bond_delay_days': 0, 'bond_amount': 4000},
        ]

        result = scan_anomalies(contracts)

        # Sort by id to check easily
        result_dict = {item['id']: item for item in result}

        # ID 3 should be flagged RED (top 3% of 10 is 1 item)
        self.assertEqual(result_dict[3]['status_color'], 'RED')
        self.assertIn("合同距离到期仅剩15.0天", result_dict[3]['anomaly_reason'])
        self.assertIn("履约保证金逾期5.0天未退回", result_dict[3]['anomaly_reason'])

        # All others should be GREEN
        for i in range(1, 11):
            if i != 3:
                self.assertEqual(result_dict[i]['status_color'], 'GREEN')
                self.assertEqual(result_dict[i]['anomaly_reason'], '未发现异常，静默放行')

    def test_top_3_percent_split(self):
        """Test exactly 100 items to see if top 3 items are flagged."""
        contracts = []
        for i in range(1, 101):
            contracts.append({
                'id': i,
                'days_to_expiry': i + 100, # 101 to 200
                'bond_delay_days': 0,
                'bond_amount': 1000
            })

        # Make exactly 3 items extremely risky
        contracts[4]['days_to_expiry'] = 5
        contracts[4]['bond_delay_days'] = 10
        contracts[4]['bond_amount'] = 50000

        contracts[50]['days_to_expiry'] = 10
        contracts[50]['bond_delay_days'] = 5
        contracts[50]['bond_amount'] = 30000

        contracts[99]['days_to_expiry'] = 15
        contracts[99]['bond_delay_days'] = 2
        contracts[99]['bond_amount'] = 20000

        result = scan_anomalies(contracts)

        red_count = sum(1 for item in result if item['status_color'] == 'RED')
        yellow_count = sum(1 for item in result if item['status_color'] == 'YELLOW')
        green_count = sum(1 for item in result if item['status_color'] == 'GREEN')

        # 3% of 100 is 3. The logic gives the highest one RED, others RED/YELLOW depending on score,
        # but combined they should be exactly 3.
        self.assertEqual(red_count + yellow_count, 3)
        self.assertEqual(green_count, 97)

        result_dict = {item['id']: item for item in result}
        self.assertIn(result_dict[5]['status_color'], ['RED', 'YELLOW'])
        self.assertIn(result_dict[51]['status_color'], ['RED', 'YELLOW'])
        self.assertIn(result_dict[100]['status_color'], ['RED', 'YELLOW'])

    def test_missing_parameters_self_healing(self):
        """Test with completely missing parameters to ensure self-healing provides valid defaults and doesn't crash."""
        contracts = [
            {'id': 1}, # Missing everything
            {'id': 2, 'days_to_expiry': None, 'bond_delay_days': None, 'bond_amount': None},
            {'id': 3, 'days_to_expiry': 10} # High risk but missing bond info
        ]

        result = scan_anomalies(contracts)
        result_dict = {item['id']: item for item in result}

        # Default behavior: missing expiry is treated as safe (999.0)
        self.assertEqual(result_dict[1]['status_color'], 'GREEN')
        self.assertEqual(result_dict[2]['status_color'], 'GREEN')
        # Item 3 is highest risk, top 3% of 3 is 1.
        self.assertEqual(result_dict[3]['status_color'], 'RED')
        self.assertIn("仅剩10.0天", result_dict[3]['anomaly_reason'])

    def test_dirty_data_type_casting(self):
        """Test with dirty strings that should be cast to floats."""
        contracts = [
            {'id': 1, 'days_to_expiry': '30', 'bond_delay_days': '2', 'bond_amount': '15000'},
            {'id': 2, 'days_to_expiry': 'invalid', 'bond_delay_days': 'N/A', 'bond_amount': 'abc'},
            {'id': 3, 'days_to_expiry': ' 45 ', 'bond_delay_days': 0, 'bond_amount': None}
        ]

        result = scan_anomalies(contracts)
        result_dict = {item['id']: item for item in result}

        # ID 1 has the highest risk because 30 days is near expiry
        self.assertEqual(result_dict[1]['status_color'], 'RED')

        # ID 2 should be self-healed to defaults: dte=999, delay=0, amount=0 -> Safe
        self.assertEqual(result_dict[2]['status_color'], 'GREEN')

    def test_extreme_boundary_dataset(self):
        """Test with extreme values, like negative days (already expired) and massive amounts."""
        contracts = [
            {'id': 1, 'days_to_expiry': -100, 'bond_delay_days': 300, 'bond_amount': 999999999}, # Extremely bad
            {'id': 2, 'days_to_expiry': 9999, 'bond_delay_days': 0, 'bond_amount': 0}, # Extremely safe
            {'id': 3, 'days_to_expiry': 0, 'bond_delay_days': 0, 'bond_amount': 100}, # Right at expiry
        ]

        result = scan_anomalies(contracts)
        result_dict = {item['id']: item for item in result}

        # Top 3% of 3 is ceil(0.09) = 1. So only 1 item should be flagged.
        self.assertEqual(result_dict[1]['status_color'], 'RED')
        self.assertIn("紧急关注", result_dict[1]['anomaly_reason'])
        self.assertEqual(result_dict[2]['status_color'], 'GREEN')
        self.assertEqual(result_dict[3]['status_color'], 'GREEN')

    def test_zero_risk_handling(self):
        """Test when absolutely no risks exist, should still handle without crashing and flag GREEN."""
        contracts = [
            {'id': 1, 'days_to_expiry': 200, 'bond_delay_days': 0, 'bond_amount': 0},
            {'id': 2, 'days_to_expiry': 300, 'bond_delay_days': 0, 'bond_amount': 0},
        ]

        result = scan_anomalies(contracts)

        for item in result:
            self.assertEqual(item['status_color'], 'GREEN')
            self.assertEqual(item['anomaly_reason'], '未发现异常，静默放行')

if __name__ == '__main__':
    unittest.main()
