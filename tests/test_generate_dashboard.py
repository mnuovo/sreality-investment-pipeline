import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sreality_investment_pipeline.generate_dashboard import build_investment_section, build_markdown_dashboard, build_rental_section, investment_status, load_analyzed_data


class RentalReportTests(unittest.TestCase):
    def test_no_candidates_replaces_old_report_with_empty_result(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            report.write_text("Old investment report")
            with patch("sreality_investment_pipeline.generate_dashboard.REPORT_PATH", report):
                build_markdown_dashboard([])
            content = report.read_text(encoding="utf-8")
            self.assertIn("No investment candidates", content)
            self.assertNotIn("Old investment report", content)

    def setUp(self):
        self.property = {
            "id": 1, "name": "Sale 2+kk 60 m\u00b2", "seo_url": "https://example.com/sale/1",
            "price": 6000000, "locality": "City", "deviation_pct": -10,
            "rental_estimate": {
                "status": "estimated", "monthly_rent_czk": 20000, "annual_rent_czk": 240000,
                "monthly_rent_range_czk": [18000, 22000], "gross_annual_yield_pct": 4.0,
                "comparable_count": 3, "comparable_ids": [2, 3, 4],
                "comparable_urls": ["https://example.com/2", "https://example.com/3", "https://example.com/4"],
            },
        }

    def test_report_shows_yield_assumptions_and_sources(self):
        content = build_rental_section([self.property])
        for text in ("20,000", "240,000", "4.00%", "not net ROI", "https://example.com/2", "Pending"):
            self.assertIn(text, content)

    def test_missing_estimate_is_not_reported_as_zero_yield(self):
        property_meta = {**self.property, "rental_estimate": {"status": "insufficient_data", "comparable_count": 2}}
        content = build_rental_section([property_meta])
        self.assertIn("Insufficient data", content)
        self.assertNotIn("0.00%", content)

    def test_pending_candidates_can_generate_report_without_ai_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.md"
            with patch("sreality_investment_pipeline.generate_dashboard.REPORT_PATH", report):
                build_markdown_dashboard([self.property])
            content = report.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# 🏰 Property Investment Dashboard\n"))
            self.assertNotIn("Poděbrady", content.splitlines()[0])
            self.assertIn("AI Review Pending:** 1", content)
            self.assertIn("Yes Verdicts:** 0", content)
            self.assertIn("4.00%", content)

    def test_legacy_boolean_does_not_claim_investment_approval(self):
        property_meta = {**self.property, "ai_evaluation": {"is_good_investment": True, "summary_verdict": "Old positive screen"}}
        self.assertEqual(investment_status(property_meta), "Needs reanalysis")
        content = build_investment_section([property_meta])
        self.assertIn("old boolean risk screen is not investment approval", content)
        self.assertNotIn("Old positive screen", content)

    def test_expanded_analysis_renders_all_four_requested_areas(self):
        review = {
            "investment_verdict": "Conditional", "property_location_overview": "Location evidence",
            "potential_rent_market_demand": "Demand evidence and SVJ caveats", "financial_roi_analysis": "Cost gaps",
            "summary_verdict": "Further checks required", "top_risks": ["Unknown HOA"],
            "top_upside_potentials": ["Asking discount"], "missing_information": ["Insurance quote"],
        }
        content = build_investment_section([{**self.property, "ai_evaluation": review}])
        for text in ("1. Property & Location", "2. Potential Rent", "3. Financial & ROI", "4. Investment Verdict", "Unknown HOA", "Insurance quote", "4.5%", "5.0%", "acquisition tax was abolished"):
            self.assertIn(text, content)

    def test_current_missing_costs_prevent_unqualified_yes_in_report(self):
        property_meta = {**self.property, "ai_evaluation": {"investment_verdict": "Yes"}}
        self.assertEqual(investment_status(property_meta), "Conditional")

    def test_empty_ai_analysis_falls_back_to_candidates_without_invented_rent(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis, candidates, rentals = (Path(directory) / name for name in ("analysis.json", "candidates.json", "missing.json"))
            analysis.write_text("[]")
            candidates.write_text(json.dumps([self.property]))
            with patch("sreality_investment_pipeline.generate_dashboard.ANALYSIS_PATH", analysis), \
                    patch("sreality_investment_pipeline.generate_dashboard.CANDIDATES_PATH", candidates), \
                    patch("sreality_investment_pipeline.generate_dashboard.RENTAL_OUTPUT_PATH", rentals):
                properties = load_analyzed_data()
            self.assertEqual(properties[0]["rental_estimate"]["status"], "insufficient_data")
            self.assertNotIn("ai_evaluation", properties[0])

    def test_report_uses_current_candidates_and_retains_matching_ai_reviews(self):
        with tempfile.TemporaryDirectory() as directory:
            analysis, candidates, rentals = (Path(directory) / name for name in ("analysis.json", "candidates.json", "missing.json"))
            review = {"is_good_investment": False, "summary_verdict": "Existing model assessment."}
            analysis.write_text(json.dumps([
                {**self.property, "price": 9000000, "ai_evaluation": review},
                {**self.property, "id": 99, "ai_evaluation": review},
            ]))
            candidates.write_text(json.dumps([{**self.property, "price": 5500000}]))
            with patch("sreality_investment_pipeline.generate_dashboard.ANALYSIS_PATH", analysis), \
                    patch("sreality_investment_pipeline.generate_dashboard.CANDIDATES_PATH", candidates), \
                    patch("sreality_investment_pipeline.generate_dashboard.RENTAL_OUTPUT_PATH", rentals):
                properties = load_analyzed_data()
        self.assertEqual(len(properties), 1)
        self.assertEqual(properties[0]["price"], 5500000)
        self.assertEqual(properties[0]["ai_evaluation"], review)


if __name__ == "__main__":
    unittest.main()