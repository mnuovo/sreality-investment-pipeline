import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sreality_investment_pipeline.math_filter_engine import estimate_rental_income


class RentalEstimateTests(unittest.TestCase):
    def test_missing_sale_snapshot_returns_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sreality_investment_pipeline.paths.RAW_OUTPUT_PATH", Path(directory) / "missing.json"), patch.dict(sys.modules):
                sys.modules.pop("sreality_investment_pipeline.math_filter_engine", None)
                with self.assertRaises(SystemExit) as failure:
                    runpy.run_module("sreality_investment_pipeline.math_filter_engine", run_name="__main__")
        self.assertEqual(failure.exception.code, 1)

    def setUp(self):
        self.property = {"name": "Prodej bytu 2+kk 60 m\u00b2", "price": 6000000, "locality": "Street, City"}
        self.rentals = [{
            "id": index, "name": "Pronajem bytu 2+kk 60 m\u00b2", "price": price,
            "locality": "Other street, City - Center", "price_period": "month",
            "rent_excludes_utilities": True, "seo_url": f"https://example.com/{index}",
        } for index, price in enumerate((18000, 20000, 60000), 1)]

    def test_median_rent_and_gross_yield(self):
        estimate = estimate_rental_income(self.property, self.rentals)
        self.assertEqual(estimate["status"], "estimated")
        self.assertEqual(estimate["monthly_rent_czk"], 20000)
        self.assertEqual(estimate["annual_rent_czk"], 240000)
        self.assertEqual(estimate["gross_annual_yield_pct"], 4.0)
        self.assertEqual(estimate["comparable_count"], 3)

    def test_insufficient_sample_does_not_invent_rent(self):
        estimate = estimate_rental_income(self.property, self.rentals[:2])
        self.assertEqual(estimate["comparable_count"], 2)
        self.assertIsNone(estimate["monthly_rent_czk"])
        self.assertIsNone(estimate["gross_annual_yield_pct"])

    def test_duplicate_ids_do_not_inflate_sample(self):
        estimate = estimate_rental_income(self.property, self.rentals[:2] * 2)
        self.assertEqual(estimate["comparable_count"], 2)

    def test_incomparable_or_unverified_rents_are_excluded(self):
        for changes in (
            {"locality": "Other city"}, {"name": "Pronajem bytu 3+kk 60 m\u00b2"},
            {"name": "Pronajem bytu 2+kk 100 m\u00b2"}, {"price_period": "day"},
            {"rent_excludes_utilities": False}, {"rent_excludes_utilities": None},
            {"price": 0}, {"price": "20000"}, {"price": float("nan")},
        ):
            with self.subTest(changes=changes):
                rentals = [{**rental, **changes} for rental in self.rentals]
                self.assertEqual(estimate_rental_income(self.property, rentals)["comparable_count"], 0)

    def test_area_normalized_rent(self):
        rentals = [{**rental, "name": "Pronajem bytu 2+kk 50 m\u00b2", "price": 15000} for rental in self.rentals]
        self.assertEqual(estimate_rental_income(self.property, rentals)["monthly_rent_czk"], 18000)

    def test_invalid_purchase_price_has_no_yield(self):
        estimate = estimate_rental_income({**self.property, "price": 0}, self.rentals)
        self.assertEqual(estimate["monthly_rent_czk"], 20000)
        self.assertIsNone(estimate["gross_annual_yield_pct"])

    def test_older_sale_snapshot_matches_new_rental_metadata(self):
        rentals = [{**rental, "municipality_id": 4121} for rental in self.rentals]
        self.assertEqual(estimate_rental_income(self.property, rentals)["comparable_count"], 3)

    def test_different_municipality_ids_are_not_matched(self):
        rentals = [{**rental, "municipality_id": 4122} for rental in self.rentals]
        sale = {**self.property, "municipality_id": 4121}
        self.assertEqual(estimate_rental_income(sale, rentals)["comparable_count"], 0)


if __name__ == "__main__":
    unittest.main()