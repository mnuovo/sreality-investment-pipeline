import asyncio
import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from sreality_investment_pipeline.scraper_harness import (
    RENTAL_SEARCH_URL, SALE_SEARCH_URL, extract_page_count, extract_rental_terms,
    build_argument_parser, enrich_rental_listings, run_harness, scrape_markets, scrape_sreality_page,
)

class SearchArgumentTests(unittest.TestCase):
    def test_defaults_preserve_current_filters(self):
        args = build_argument_parser().parse_args([])
        self.assertEqual(args.sale_url, SALE_SEARCH_URL)
        self.assertEqual(args.rental_url, RENTAL_SEARCH_URL)

    def test_url_overrides_preserve_query_strings(self):
        sale = "https://www.sreality.cz/hledani/prodej/byty?region=Praha&vlastnictvi=osobni"
        rental = "https://sreality.cz/hledani/pronajem/byty/praha?velikost=2%2Bkk&strana=1"
        args = build_argument_parser().parse_args(["--sale-url", sale, "--rental-url", rental])
        self.assertEqual(args.sale_url, sale)
        self.assertEqual(args.rental_url, rental)

    def test_invalid_or_wrong_market_urls_are_rejected(self):
        for flag, url in (
            ("--sale-url", "http://www.sreality.cz/hledani/prodej/byty"),
            ("--sale-url", "https://example.com/hledani/prodej/byty"),
            ("--sale-url", "https://www.sreality.cz.evil.example/hledani/prodej/byty"),
            ("--sale-url", RENTAL_SEARCH_URL),
            ("--rental-url", SALE_SEARCH_URL),
            ("--rental-url", "https://www.sreality.cz/hledani/pronajem/domy"),
            ("--sale-url", "https://user:password@www.sreality.cz/hledani/prodej/byty"),
            ("--sale-url", "https://www.sreality.cz:invalid/hledani/prodej/byty"),
            ("--sale-url", "https://www.sreality.cz/hledani/prodej/byty?region=Bad space"),
        ):
            with self.subTest(flag=flag, url=url), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as failure:
                    build_argument_parser().parse_args([flag, url])
                self.assertEqual(failure.exception.code, 2)


class RentalTermsTests(unittest.TestCase):
    def test_explicit_additional_charges_are_excluded(self):
        detail = {"description": "Najem 18000 Kc + poplatky 3000 Kc.", "priceUnitCb": {"value": 2}}
        terms = extract_rental_terms(detail)
        self.assertTrue(terms["rent_excludes_utilities"])
        self.assertEqual(terms["price_period"], "month")

    def test_inclusive_price_is_not_base_rent(self):
        detail = {"description": "Cena vcetne energii a sluzeb. + poplatky za parkovani.", "priceUnitCb": {"value": 2}}
        self.assertFalse(extract_rental_terms(detail)["rent_excludes_utilities"])

    def test_unknown_charges_and_nonmonthly_period_are_not_assumed(self):
        terms = extract_rental_terms({"description": "Krasny byt k pronajmu.", "priceUnitCb": {"value": 3}})
        self.assertIsNone(terms["rent_excludes_utilities"])
        self.assertEqual(terms["price_period"], "unknown")

    def test_itemized_monthly_charges(self):
        detail = {"description": "Mesicni najemne: 15900 Kc. Pausalni mesicni poplatky: 4300 Kc."}
        self.assertTrue(extract_rental_terms(detail)["rent_excludes_utilities"])


class MarketScrapingTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_urls_reach_harness_unchanged(self):
        sale_url = "https://www.sreality.cz/hledani/prodej/byty?region=Praha&vlastnictvi=osobni"
        rental_url = "https://www.sreality.cz/hledani/pronajem/byty?region=Praha&vlastnictvi=osobni"
        scrape = AsyncMock(return_value=[])
        with tempfile.TemporaryDirectory() as directory:
            with patch("sreality_investment_pipeline.scraper_harness.RAW_OUTPUT_PATH", Path(directory) / "sale.json"), \
                    patch("sreality_investment_pipeline.scraper_harness.RENTAL_OUTPUT_PATH", Path(directory) / "rent.json"), \
                    patch("sreality_investment_pipeline.scraper_harness.run_harness", scrape):
                await scrape_markets(sale_url=sale_url, rental_url=rental_url)
        self.assertEqual([call.args[0] for call in scrape.await_args_list], [sale_url, rental_url])

    async def test_invalid_rental_url_fails_before_any_market_is_collected(self):
        with patch("sreality_investment_pipeline.scraper_harness.run_harness", new_callable=AsyncMock) as scrape:
            with self.assertRaises(argparse.ArgumentTypeError):
                await scrape_markets(rental_url="https://example.com/invalid")
        scrape.assert_not_awaited()

    async def test_rental_details_refresh_price_and_verify_terms(self):
        listing = {"id": 123, "price": 15000, "seo_url": "https://www.sreality.cz/detail/pronajem/123"}
        page = Mock()
        page.url = listing["seo_url"]
        page.goto = AsyncMock(return_value=Mock(status=200))
        state = {"props": {"pageProps": {"dehydratedState": {"queries": [{
            "queryKey": ["estate", {"id": 123}],
            "state": {"data": {"priceCzk": 17000, "priceUnitCb": {"value": 2}, "description": "Najem + energie."}},
        }]}}}}
        page.locator.return_value.inner_text = AsyncMock(return_value=json.dumps(state))
        await enrich_rental_listings(page, [listing])
        self.assertEqual(listing["price"], 17000)
        self.assertEqual(listing["price_period"], "month")
        self.assertTrue(listing["rent_excludes_utilities"])

    async def test_both_markets_have_separate_outputs_and_filters(self):
        scrape = AsyncMock(side_effect=[[{"id": 1}], [{"id": 2}]])
        with tempfile.TemporaryDirectory() as directory:
            sale = Path(directory) / "sale.json"
            rent = Path(directory) / "rent.json"
            with patch("sreality_investment_pipeline.scraper_harness.RAW_OUTPUT_PATH", sale), \
                    patch("sreality_investment_pipeline.scraper_harness.RENTAL_OUTPUT_PATH", rent), \
                    patch("sreality_investment_pipeline.scraper_harness.run_harness", scrape):
                await scrape_markets()
            self.assertEqual(json.loads(sale.read_text()), [{"id": 1}])
            self.assertEqual(json.loads(rent.read_text()), [{"id": 2}])
        self.assertEqual(scrape.await_args_list[0].args[0], SALE_SEARCH_URL)
        self.assertEqual(scrape.await_args_list[1].args[0], RENTAL_SEARCH_URL)
        self.assertFalse(scrape.await_args_list[0].kwargs["rental_details"])
        self.assertTrue(scrape.await_args_list[1].kwargs["rental_details"])


def pagination_state(pagination):
    return {"props": {"pageProps": {"dehydratedState": {"queries": [{
        "queryKey": ["estatesSearch"],
        "state": {"data": {"pagination": pagination}},
    }]}}}}


class PaginationTests(unittest.TestCase):
    def test_page_count_uses_pagination_slots_including_promotions(self):
        state = pagination_state({"total": 40, "totalWithPromo": 45, "limit": 22})
        self.assertEqual(extract_page_count(state), 3)

    def test_page_count_falls_back_to_regular_total(self):
        state = pagination_state({"total": 28, "limit": 22})
        self.assertEqual(extract_page_count(state), 2)

    def test_exact_page_boundary_does_not_add_a_page(self):
        state = pagination_state({"total": 44, "limit": 22})
        self.assertEqual(extract_page_count(state), 2)

    def test_zero_results_require_only_the_initial_page(self):
        self.assertEqual(extract_page_count(pagination_state({"total": 0, "limit": 22})), 1)

    def test_invalid_metadata_is_an_error(self):
        for pagination in ({}, {"total": 28, "limit": 0}, {"total": -1, "limit": 22}):
            with self.subTest(pagination=pagination):
                with self.assertRaisesRegex(RuntimeError, "page count"):
                    extract_page_count(pagination_state(pagination))


class HarnessPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_automatic_pagination_preserves_filters_and_deduplicates(self):
        page = Mock()
        page.locator.return_value.inner_text = AsyncMock(
            return_value=json.dumps(pagination_state({"total": 28, "totalWithPromo": 31, "limit": 22}))
        )
        context = Mock()
        context.new_page = AsyncMock(return_value=page)
        browser = Mock()
        browser.new_context = AsyncMock(return_value=context)
        browser.close = AsyncMock()
        playwright = Mock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=playwright)
        manager.__aexit__ = AsyncMock(return_value=False)
        scrape = AsyncMock(side_effect=[[{"id": 1}, {"id": 2}], [{"id": 2}, {"id": 3}]])

        with patch("sreality_investment_pipeline.scraper_harness.async_playwright", return_value=manager), \
            patch("sreality_investment_pipeline.scraper_harness.scrape_sreality_page", scrape), \
            patch("sreality_investment_pipeline.scraper_harness.asyncio.sleep", new_callable=AsyncMock):
            results = await run_harness("https://www.sreality.cz/hledani/prodej/byty?region-id=4121&strana=9")

        self.assertEqual(results, [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(scrape.await_count, 2)
        self.assertEqual(scrape.await_args_list[0].args[1],
                         "https://www.sreality.cz/hledani/prodej/byty?region-id=4121&strana=1")
        self.assertEqual(scrape.await_args_list[1].args[1],
                         "https://www.sreality.cz/hledani/prodej/byty?region-id=4121&strana=2")
        browser.close.assert_awaited_once()


class ScrapePageTests(unittest.IsolatedAsyncioTestCase):
    def make_page(self, payload=None, final_url="https://www.sreality.cz/hledani/prodej/byty"):
        page = Mock()
        page.url = final_url
        page.locator.return_value.count = AsyncMock(return_value=0)
        response = Mock()
        response.url = "https://www.sreality.cz/api/cs/v2/estates"
        response.status = 200
        response.json = AsyncMock(return_value=payload)

        async def navigate(*args, **kwargs):
            if payload is not None:
                callback = page.on.call_args.args[1]
                await callback(response)

        page.goto = AsyncMock(side_effect=navigate)
        return page

    async def test_embedded_search_data_without_api_request(self):
        page = self.make_page()
        state = {"props": {"pageProps": {"dehydratedState": {"queries": [{
            "queryKey": ["estatesSearch", {"page": 1}],
            "state": {"data": {"results": [{
                "id": 123,
                "name": "Prodej bytu 2+kk",
                "priceCzk": 5000000,
                "locality": {"city": "Podebrady", "latitude": 50.14, "longitude": 15.12},
            }]}},
        }]}}}}
        detail_url = "https://www.sreality.cz/detail/prodej/byt/2+kk/podebrady/123"
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.inner_text = AsyncMock(return_value=json.dumps(state))
        page.locator.return_value.evaluate_all = AsyncMock(return_value=[detail_url])

        results = await scrape_sreality_page(page, page.url)
        self.assertEqual(results, [{
            "id": 123,
            "name": "Prodej bytu 2+kk",
            "price": 5000000,
            "locality": "Podebrady",
            "gps": {"lat": 50.14, "lon": 15.12},
            "seo_url": detail_url,
        }])
        page.remove_listener.assert_called_once()

    async def test_empty_embedded_search_is_valid(self):
        page = self.make_page()
        state = {"props": {"pageProps": {"dehydratedState": {"queries": [{
            "queryKey": ["estatesSearch"],
            "state": {"data": {"results": []}},
        }]}}}}
        page.locator.return_value.count.return_value = 1
        page.locator.return_value.inner_text = AsyncMock(return_value=json.dumps(state))
        page.locator.return_value.evaluate_all = AsyncMock(return_value=[])
        self.assertEqual(await scrape_sreality_page(page, page.url), [])

    async def test_capture_does_not_require_property_selector(self):
        page = self.make_page({"_embedded": {"estates": [{
            "hash_id": 123,
            "name": "Prodej bytu 2+kk",
            "price_czk": {"value_raw": 5000000},
            "locality": "Podebrady",
            "gps": {"lat": 50.14, "lon": 15.12},
        }]}})
        results = await scrape_sreality_page(page, page.url)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], 123)
        self.assertEqual(results[0]["price"], 5000000)
        page.wait_for_selector.assert_not_called()
        page.remove_listener.assert_called_once_with("response", page.on.call_args.args[1])

    async def test_empty_api_response_is_a_valid_empty_search(self):
        page = self.make_page({"_embedded": {"estates": []}})
        self.assertEqual(await scrape_sreality_page(page, page.url), [])

    async def test_consent_redirect_is_actionable_error(self):
        page = self.make_page(final_url="https://cmp.seznam.cz/nastaveni-souhlasu")
        with self.assertRaisesRegex(RuntimeError, "--setup-consent"):
            await scrape_sreality_page(page, page.url)
        page.remove_listener.assert_called_once()

    async def test_missing_api_response_is_not_an_empty_search(self):
        page = self.make_page()

        async def timeout(awaitable, **kwargs):
            awaitable.close()
            raise asyncio.TimeoutError

        with patch("sreality_investment_pipeline.scraper_harness.asyncio.wait_for", side_effect=timeout):
            with self.assertRaisesRegex(RuntimeError, "No estate API payload"):
                await scrape_sreality_page(page, page.url)
        page.remove_listener.assert_called_once()

    async def test_navigation_errors_propagate_and_remove_listener(self):
        page = self.make_page()
        page.goto.side_effect = RuntimeError("Navigation failed")
        with self.assertRaisesRegex(RuntimeError, "Navigation failed"):
            await scrape_sreality_page(page, page.url)
        page.remove_listener.assert_called_once()


if __name__ == "__main__":
    unittest.main()