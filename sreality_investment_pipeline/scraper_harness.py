import argparse
import asyncio
import json
import random
import re
import unicodedata
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from playwright.async_api import async_playwright

from .paths import DEFAULT_STATE_PATH, RAW_OUTPUT_PATH, RENTAL_OUTPUT_PATH

SALE_SEARCH_URL = "https://www.sreality.cz/hledani/prodej/byty?stavba=cihlova%2Costatni&vlastnictvi=osobni&region=Pod%C4%9Bbrady&region-id=4121&region-typ=municipality"
RENTAL_SEARCH_URL = "https://www.sreality.cz/hledani/pronajem/byty?vlastnictvi=osobni&region=Pod%C4%9Bbrady&region-id=4121&region-typ=municipality"

BROWSER_OPTIONS = {
    "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "viewport": {"width": 1440, "height": 900},
    "locale": "cs-CZ",
    "timezone_id": "Europe/Prague",
}

def extract_embedded_listings(state, detail_urls):
    queries = state.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for query in queries:
        query_key = query.get("queryKey", [])
        if not query_key or query_key[0] != "estatesSearch":
            continue
        data = query.get("state", {}).get("data") or {}
        if not isinstance(data.get("results"), list):
            return None

        listings = []
        for estate in data["results"]:
            locality = estate.get("locality") or {}
            city = locality.get("city") or ""
            city_part = locality.get("cityPart")
            if city_part and city_part != city:
                city = f"{city} - {city_part}" if city else city_part
            address = ", ".join(part for part in (locality.get("street"), city) if part)
            listing = {
                "id": estate["id"],
                "name": estate.get("name"),
                "price": estate.get("priceCzk"),
                "locality": address,
                "gps": {"lat": locality.get("latitude"), "lon": locality.get("longitude")},
                "seo_url": detail_urls.get(str(estate["id"])),
            }
            if locality.get("municipalityId") is not None:
                listing["municipality_id"] = locality["municipalityId"]
            if estate.get("categoryTypeCb", {}).get("value") == 2:
                listing["price_period"] = "month" if estate.get("priceUnitCb", {}).get("value") == 2 else "unknown"
                listing["rent_excludes_utilities"] = None
            listings.append(listing)
        return listings
    return None

def extract_rental_terms(detail):
    params = detail.get("params") or {}
    text = " ".join(str(value or "") for value in (
        detail.get("description"), params.get("priceNote"), params.get("costOfLiving"),
    ))
    text = "".join(character for character in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(character))
    text = re.sub(r"\s+", " ", text)
    inclusive = re.search(r"(?:vcetne|vc\.|v cene)[^.!?]{0,60}(?:poplat|sluz|energ|inkas)", text)
    separate = re.search(r"(?:\+|plus|bez|nezahrnuje)[^.!?]{0,40}(?:poplat|sluz|energ|inkas)", text)
    itemized = re.search(r"mesicni najemne", text) and re.search(r"(?:pausalni|mesicni) (?:mesicni )?poplatky", text)
    return {
        "price_period": "month" if (detail.get("priceUnitCb") or {}).get("value") == 2 else "unknown",
        "rent_excludes_utilities": False if inclusive else True if separate or itemized else None,
        "rent_terms_note": params.get("priceNote"),
        "rent_terms_checked_at": datetime.now(timezone.utc).isoformat(),
    }

async def enrich_rental_listings(page, listings):
    for index, listing in enumerate(listings):
        listing["rent_excludes_utilities"] = None
        if not listing.get("seo_url"):
            continue
        try:
            response = await page.goto(listing["seo_url"], wait_until="domcontentloaded", timeout=30000)
            if urllib.parse.urlparse(page.url).hostname == "cmp.seznam.cz":
                raise RuntimeError("Consent expired while reading rental details; rerun consent setup.")
            if response is not None and response.status >= 400:
                raise ValueError(f"HTTP {response.status}")
            state = json.loads(await page.locator("script#__NEXT_DATA__").inner_text())
            queries = state.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
            for query in queries:
                key = query.get("queryKey", [])
                if key and key[0] == "estate" and len(key) > 1 and str(key[1].get("id")) == str(listing["id"]):
                    detail = query.get("state", {}).get("data") or {}
                    listing.update(extract_rental_terms(detail))
                    listing["price"] = detail.get("priceCzk")
                    break
            print(f"[*] Checked rental terms {index + 1}/{len(listings)}: {listing['id']}")
        except RuntimeError:
            raise
        except Exception as error:
            print(f"[-] Could not verify rental terms for {listing['id']}: {error}")
        if index + 1 < len(listings):
            await asyncio.sleep(random.uniform(1.0, 2.0))

def extract_page_count(state):
    queries = state.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for query in queries:
        query_key = query.get("queryKey", [])
        if not query_key or query_key[0] != "estatesSearch":
            continue
        data = query.get("state", {}).get("data") or {}
        pagination = data.get("pagination") or {}
        total = pagination.get("totalWithPromo", pagination.get("total"))
        limit = pagination.get("limit")
        if isinstance(total, int) and total >= 0 and isinstance(limit, int) and limit > 0:
            return max(1, (total + limit - 1) // limit)
        break
    raise RuntimeError("Could not determine the page count from Sreality pagination metadata.")

async def scrape_sreality_page(page, target_url):
    """
    Read server-rendered search data, falling back to legacy API responses.
    """
    captured_data = []
    data_captured_event = asyncio.Event()
    
    async def handle_response(response):
        # Target the definitive v2 data engine endpoint used by Seznam
        if "/v2/estates" in response.url and response.status == 200:
            try:
                json_payload = await response.json()
                if "_embedded" in json_payload and "estates" in json_payload["_embedded"]:
                    estates = json_payload["_embedded"]["estates"]
                    for estate in estates:
                        captured_data.append({
                            "id": estate.get("hash_id"),
                            "name": estate.get("name"),
                            "price": estate.get("price_czk", {}).get("value_raw") or estate.get("price"),
                            "locality": estate.get("locality"),
                            "gps": estate.get("gps", {}),
                            "seo_url": f"https://sreality.cz{estate.get('seo', {}).get('locality')}/{estate.get('hash_id')}"
                        })
                    print(f"[+] Successfully sniffed {len(estates)} properties from API payload.")
                    data_captured_event.set()
            except Exception as error:
                print(f"[-] Could not decode estate response: {error}")

    page.on("response", handle_response)
    print(f"[+] Navigating to: {target_url}")
    
    try:
        await page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        if urllib.parse.urlparse(page.url).hostname == "cmp.seznam.cz":
            raise RuntimeError(
                "Seznam redirected to its consent page. Run "
                "'python -m sreality_investment_pipeline.scraper_harness --setup-consent', choose your "
                "consent preferences, then run the scraper again."
            )

        embedded_state = page.locator("script#__NEXT_DATA__")
        if await embedded_state.count():
            state = json.loads(await embedded_state.inner_text())
            detail_links = await page.locator('a[href*="/detail/"]').evaluate_all(
                "nodes => nodes.map(node => node.href)"
            )
            detail_urls = {
                urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]: url
                for url in detail_links
            }
            listings = extract_embedded_listings(state, detail_urls)
            if listings is not None:
                print(f"[+] Read {len(listings)} properties from embedded search data.")
                return listings

        print("[*] Waiting for estate data...")
        try:
            await asyncio.wait_for(data_captured_event.wait(), timeout=20.0)
        except asyncio.TimeoutError as error:
            raise RuntimeError(
                f"No estate API payload received at {page.url}. "
                "The page may be blocked or the site's data endpoint may have changed. "
                "The output file has not been overwritten."
            ) from error
    finally:
        page.remove_listener("response", handle_response)
        
    return captured_data

async def setup_consent(start_url, state_path=DEFAULT_STATE_PATH):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        try:
            context = await browser.new_context(**BROWSER_OPTIONS)
            page = await context.new_page()
            print("[*] Choose your consent preferences in the browser. Waiting up to 5 minutes...")
            await page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            target = urllib.parse.urlparse(start_url)
            await page.wait_for_url(
                lambda url: (
                    urllib.parse.urlparse(url).hostname == target.hostname
                    and urllib.parse.urlparse(url).path == target.path
                ),
                wait_until="domcontentloaded",
                timeout=300000,
            )
            state_path = Path(state_path)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.touch(mode=0o600, exist_ok=True)
            state_path.chmod(0o600)
            await context.storage_state(path=state_path)
            print(f"[+] Browser state saved to {state_path}. Run the scraper normally now.")
        finally:
            await browser.close()

async def run_harness(start_url, max_pages=None, *, state_path=DEFAULT_STATE_PATH, rental_details=False):
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be at least 1, or None for automatic pagination.")
    all_listings = []
    seen_ids = set()
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            state_path = Path(state_path)
            context = await browser.new_context(
                **BROWSER_OPTIONS,
                storage_state=str(state_path) if state_path.exists() else None,
            )
        
            page = await context.new_page()
        
            current_page = 1
            while max_pages is None or current_page <= max_pages:
                parsed_url = urllib.parse.urlparse(start_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)
                query_params['strana'] = [str(current_page)]
            
                new_query = urllib.parse.urlencode(query_params, doseq=True)
                page_url = urllib.parse.urlunparse(parsed_url._replace(query=new_query))
            
                print(f"[*] Processing page {current_page}...")
                page_listings = await scrape_sreality_page(page, page_url)

                if max_pages is None:
                    state = json.loads(await page.locator("script#__NEXT_DATA__").inner_text())
                    max_pages = extract_page_count(state)
                    print(f"[+] Sreality reports {max_pages} search pages. Collecting all pages.")
            
                if not page_listings:
                    print("[-] No listings detected on this node slice. Terminating sequence.")
                    break
                
                for listing in page_listings:
                    if listing["id"] not in seen_ids:
                        seen_ids.add(listing["id"])
                        all_listings.append(listing)
                current_page += 1
                if current_page <= max_pages:
                    await asyncio.sleep(random.uniform(2.0, 4.0))

            if rental_details:
                await enrich_rental_listings(page, all_listings)
            
        finally:
            await browser.close()
        
    return all_listings

def validate_search_url(value, market):
    path = "/hledani/prodej/byty" if market == "sale" else "/hledani/pronajem/byty"
    try:
        parsed = urllib.parse.urlparse(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in ("www.sreality.cz", "sreality.cz")
            and parsed.port in (None, 443)
            and parsed.username is None and parsed.password is None
            and (parsed.path.rstrip("/") == path or parsed.path.startswith(path + "/"))
            and not any(character.isspace() for character in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError(
            f"Expected an HTTPS Sreality {market} apartment search URL under '{path}'."
        )
    return value

def build_argument_parser():
    parser = argparse.ArgumentParser(description="Capture Sreality search listings.")
    parser.add_argument("--market", choices=("sale", "rent", "all"), default="all", help="Collect sales, rentals, or both (default).")
    parser.add_argument("--sale-url", default=SALE_SEARCH_URL, type=lambda value: validate_search_url(value, "sale"), help="Sreality apartment sale search URL (default: Podebrady filter).")
    parser.add_argument("--rental-url", default=RENTAL_SEARCH_URL, type=lambda value: validate_search_url(value, "rent"), help="Sreality apartment rental search URL (default: Podebrady filter).")
    parser.add_argument("--setup-consent", action="store_true", help="Open a browser to save your consent preferences.")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_PATH, help="Path to private browser state.")
    return parser

async def scrape_markets(market="all", *, state_path=DEFAULT_STATE_PATH, sale_url=SALE_SEARCH_URL, rental_url=RENTAL_SEARCH_URL):
    searches = {
        "sale": (validate_search_url(sale_url, "sale"), RAW_OUTPUT_PATH),
        "rent": (validate_search_url(rental_url, "rent"), RENTAL_OUTPUT_PATH),
    }
    for name in searches if market == "all" else (market,):
        url, output = searches[name]
        print(f"[*] Collecting {name} listings...")
        listings = await run_harness(url, state_path=state_path, rental_details=name == "rent")
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as stream:
            json.dump(listings, stream, indent=4, ensure_ascii=False)
        print(f"[+] Exported {len(listings)} {name} listings to '{output}'")

if __name__ == "__main__":
    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        if args.setup_consent:
            asyncio.run(setup_consent(args.rental_url if args.market == "rent" else args.sale_url, args.state_file))
            parser.exit()
        asyncio.run(scrape_markets(args.market, state_path=args.state_file, sale_url=args.sale_url, rental_url=args.rental_url))
    except Exception as error:
        parser.exit(1, f"[-] Scrape failed: {error}\n")
