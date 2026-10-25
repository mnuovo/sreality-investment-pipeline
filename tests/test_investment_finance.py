import unittest

from sreality_investment_pipeline.investment_finance import calculate_investment_scenarios


class InvestmentFinanceTests(unittest.TestCase):
    def setUp(self):
        self.property = {
            "price": 6000000,
            "rental_estimate": {"status": "estimated", "monthly_rent_czk": 25000},
            "investment_assumptions": {
                "legal_costs_czk": 30000, "initial_repairs_czk": 70000,
                "monthly_hoa_czk": 2000, "monthly_insurance_czk": 300,
            },
        }

    def test_entry_cost_does_not_add_abolished_acquisition_tax(self):
        result = calculate_investment_scenarios(self.property)
        self.assertEqual(result["acquisition_tax_czk"], 0)
        self.assertEqual(result["total_entry_cost_czk"], 6100000)

    def test_gross_and_net_yields_use_explicit_cost_bases(self):
        result = calculate_investment_scenarios(self.property)
        self.assertEqual(result["gross_rental_yield_pct"], 5.0)
        self.assertEqual(result["annual_net_operating_income_czk"], 257400)
        self.assertEqual(result["net_rental_yield_pct"], 4.22)
        self.assertEqual(result["all_cash_cash_on_cash_return_pct"], 4.22)

    def test_mortgage_payment_and_cash_on_cash(self):
        result = calculate_investment_scenarios(self.property)
        lower, upper = result["mortgage_scenarios"]
        self.assertEqual(lower["loan_czk"], 4800000)
        self.assertEqual(lower["initial_cash_czk"], 1300000)
        self.assertAlmostEqual(lower["monthly_payment_czk"], 24320.9, delta=1)
        self.assertLess(lower["annual_cash_flow_czk"], 0)
        self.assertLess(lower["cash_on_cash_return_pct"], 0)
        self.assertGreater(upper["monthly_payment_czk"], lower["monthly_payment_czk"])

    def test_missing_rent_and_costs_are_not_invented(self):
        result = calculate_investment_scenarios({"price": 6000000})
        self.assertIsNone(result["total_entry_cost_czk"])
        self.assertIsNone(result["net_rental_yield_pct"])
        self.assertIsNone(result["mortgage_scenarios"][0]["cash_on_cash_return_pct"])
        self.assertIn("monthly_hoa_czk", result["missing_inputs"])
        self.assertIsNotNone(result["mortgage_scenarios"][0]["monthly_payment_czk"])

    def test_invalid_inputs_do_not_produce_nonfinite_yields(self):
        for price in (0, -1, True, float("nan"), "6000000"):
            with self.subTest(price=price):
                result = calculate_investment_scenarios({**self.property, "price": price})
                self.assertIsNone(result["gross_rental_yield_pct"])
                self.assertIsNone(result["mortgage_scenarios"][0]["monthly_payment_czk"])


if __name__ == "__main__":
    unittest.main()