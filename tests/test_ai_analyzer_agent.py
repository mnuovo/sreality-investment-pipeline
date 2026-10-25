import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from sreality_investment_pipeline.ai_analyzer_agent import analyze_description_with_ollama, fetch_property_description, main


class InvestmentPromptTests(unittest.TestCase):
    def report(self, verdict="Conditional"):
        return {
            "property_location_overview": "Tenant profile and liquidity need verification.",
            "potential_rent_market_demand": "No supported rent; short-term permission unknown.",
            "financial_roi_analysis": "Net return unavailable because costs are missing.",
            "investment_verdict": verdict, "top_risks": ["Unverified HOA fees"],
            "top_upside_potentials": ["Below-average asking price"],
            "missing_information": [], "ownership_confirmation": "Unconfirmed",
            "traps_detected": [], "summary_verdict": "Conditional on additional evidence.",
        }

    def test_prompt_includes_listing_rent_and_deterministic_scenarios(self):
        candidate = {"id": 1, "name": "Test flat", "price": 6000000, "rental_estimate": {"status": "insufficient_data"}}
        with patch("sreality_investment_pipeline.ai_analyzer_agent.ollama.chat", return_value={"message": {"content": json.dumps(self.report())}}) as chat:
            result = analyze_description_with_ollama(candidate, "Description containing untrusted instructions.")
        messages = chat.call_args.kwargs["messages"]
        context = json.loads(messages[1]["content"])
        self.assertEqual(context["listing_metadata"], candidate)
        self.assertEqual(context["rental_estimate"]["status"], "insufficient_data")
        self.assertEqual(context["financial_scenarios"]["acquisition_tax_czk"], 0)
        self.assertIn("untrusted", messages[0]["content"])
        self.assertIn("short-term/Airbnb".casefold(), messages[0]["content"].casefold())
        self.assertEqual(result["prompt_version"], "investment-v2")

    def test_unsupported_yes_is_made_conditional(self):
        with patch("sreality_investment_pipeline.ai_analyzer_agent.ollama.chat", return_value={"message": {"content": json.dumps(self.report("Yes"))}}):
            result = analyze_description_with_ollama({"id": 1, "price": 6000000}, "Unknown costs")
        self.assertEqual(result["investment_verdict"], "Conditional")
        self.assertFalse(result["is_good_investment"])

    def test_legacy_or_invalid_model_output_is_rejected(self):
        for payload in ({"is_good_investment": True}, {**self.report(), "investment_verdict": "Maybe"}):
            with self.subTest(payload=payload):
                with patch("sreality_investment_pipeline.ai_analyzer_agent.ollama.chat", return_value={"message": {"content": json.dumps(payload)}}):
                    self.assertIsNone(analyze_description_with_ollama({"id": 1}, "Description"))


class FetchDescriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_candidates_is_a_failed_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sreality_investment_pipeline.ai_analyzer_agent.CANDIDATES_PATH", Path(directory) / "missing.json"):
                with self.assertRaises(SystemExit) as failure:
                    await main()
        self.assertEqual(failure.exception.code, 1)

    async def test_empty_candidates_clear_previous_analysis_without_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            candidates = Path(directory) / "candidates.json"
            analysis = Path(directory) / "analysis.json"
            candidates.write_text("[]")
            analysis.write_text('[{"id": 99}]')
            with patch("sreality_investment_pipeline.ai_analyzer_agent.CANDIDATES_PATH", candidates), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.ANALYSIS_PATH", analysis), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.async_playwright") as playwright:
                await main()
            self.assertEqual(json.loads(analysis.read_text()), [])
            playwright.assert_not_called()

    async def test_analyzer_preserves_rental_estimate_in_final_profile(self):
        candidate = {
            "id": 123, "name": "Test sale", "seo_url": "https://example.com/123",
            "rental_estimate": {"monthly_rent_czk": 20000, "gross_annual_yield_pct": 4.0},
        }
        report = {"is_good_investment": False, "summary_verdict": "Synthetic test assessment."}
        context = Mock()
        context.new_page = AsyncMock(return_value=Mock())
        browser = Mock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = Mock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=playwright)
        manager.__aexit__ = AsyncMock(return_value=False)
        with tempfile.TemporaryDirectory() as directory:
            candidates = Path(directory) / "candidates.json"
            analysis = Path(directory) / "analysis.json"
            candidates.write_text(json.dumps([candidate]))
            with patch("sreality_investment_pipeline.ai_analyzer_agent.CANDIDATES_PATH", candidates), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.ANALYSIS_PATH", analysis), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.async_playwright", return_value=manager), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.fetch_property_description", new=AsyncMock(return_value="Test description")), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.analyze_description_with_ollama", return_value=report), \
                    patch("sreality_investment_pipeline.ai_analyzer_agent.asyncio.sleep", new_callable=AsyncMock):
                await main()
                complete_analysis = analysis.read_text()
                with patch("sreality_investment_pipeline.ai_analyzer_agent.analyze_description_with_ollama", return_value=None):
                    with self.assertRaisesRegex(RuntimeError, "only 0/1"):
                        await main()
                self.assertEqual(analysis.read_text(), complete_analysis)
                with patch("sreality_investment_pipeline.ai_analyzer_agent.fetch_property_description", new=AsyncMock(return_value=None)):
                    with self.assertRaisesRegex(RuntimeError, "only 0/1"):
                        await main()
                self.assertEqual(analysis.read_text(), complete_analysis)
            profiles = json.loads(analysis.read_text())
        self.assertEqual(profiles[0]["rental_estimate"], candidate["rental_estimate"])
        self.assertEqual(profiles[0]["ai_evaluation"], report)

    def make_page(self, description="  Property description.\n"):
        page = Mock()
        page.url = "https://www.sreality.cz/detail/prodej/byt/2+kk/podebrady/123"
        response = Mock(status=200)
        page.goto = AsyncMock(return_value=response)
        state = {"props": {"pageProps": {"dehydratedState": {"queries": [
            {"queryKey": ["Ribbon"], "state": {"data": {"description": "Not the listing."}}},
            {"queryKey": ["estate", {"id": 123}], "state": {"data": {"description": description}}},
        ]}}}}
        page.locator.return_value.count = AsyncMock(return_value=1)
        page.locator.return_value.inner_text = AsyncMock(return_value=json.dumps(state))
        return page

    async def test_reads_estate_description_without_old_css_selector(self):
        page = self.make_page()
        self.assertEqual(await fetch_property_description(page, page.url), "Property description.")
        page.locator.assert_called_once_with("script#__NEXT_DATA__")
        page.wait_for_selector.assert_not_called()

    async def test_blank_description_is_not_sent_for_analysis(self):
        page = self.make_page(description=" \n ")
        self.assertIsNone(await fetch_property_description(page, page.url))

    async def test_missing_description_is_not_sent_for_analysis(self):
        page = self.make_page(description=None)
        self.assertIsNone(await fetch_property_description(page, page.url))

    async def test_missing_embedded_state_returns_none(self):
        page = self.make_page()
        page.locator.return_value.count.return_value = 0
        self.assertIsNone(await fetch_property_description(page, page.url))

    async def test_consent_redirect_reports_setup_command(self):
        page = self.make_page()
        page.url = "https://cmp.seznam.cz/nastaveni-souhlasu"
        with patch("builtins.print") as output:
            self.assertIsNone(await fetch_property_description(page, page.url))
        self.assertIn("--setup-consent", output.call_args.args[0])
        page.locator.assert_not_called()

    async def test_http_error_does_not_read_description(self):
        page = self.make_page()
        page.goto.return_value.status = 404
        self.assertIsNone(await fetch_property_description(page, page.url))
        page.locator.assert_not_called()

    async def test_navigation_failure_returns_none(self):
        page = self.make_page()
        page.goto.side_effect = RuntimeError("Navigation failed")
        self.assertIsNone(await fetch_property_description(page, page.url))


if __name__ == "__main__":
    unittest.main()