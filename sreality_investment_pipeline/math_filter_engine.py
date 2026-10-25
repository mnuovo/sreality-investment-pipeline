import json
import math
import re
from collections import defaultdict
from statistics import median

from .paths import CANDIDATES_PATH, RAW_OUTPUT_PATH, RENTAL_OUTPUT_PATH

def estimate_rental_income(property_meta, rentals):
    layout, size = extract_metrics(property_meta.get("name") or "")
    estimate = {
        "status": "insufficient_data",
        "monthly_rent_czk": None,
        "annual_rent_czk": None,
        "gross_annual_yield_pct": None,
        "monthly_rent_range_czk": None,
        "comparable_count": 0,
        "comparable_ids": [],
        "comparable_urls": [],
        "method": "Median base asking rent per m2; same layout and municipality; area within 25%; minimum 3 comparables.",
        "limitations": "Advertised, not signed rents. Condition and furnishings are not adjusted. Gross yield excludes vacancy, expenses, taxes, purchase costs and financing.",
    }
    if layout == "atypical" or not size or size <= 0:
        return estimate

    def municipality(listing):
        return (listing.get("locality") or "").rsplit(",", 1)[-1].split(" - ")[0].strip().casefold()

    location = municipality(property_meta)
    if not location and property_meta.get("municipality_id") is None:
        return estimate
    comparables = []
    seen_ids = set()
    for rental in rentals:
        identifier = rental.get("id")
        price = rental.get("price")
        rental_layout, rental_size = extract_metrics(rental.get("name") or "")
        sale_municipality = property_meta.get("municipality_id")
        rent_municipality = rental.get("municipality_id")
        same_location = (
            str(sale_municipality) == str(rent_municipality)
            if sale_municipality is not None and rent_municipality is not None
            else bool(location) and municipality(rental) == location
        )
        if (
            identifier is None or identifier in seen_ids
            or rental.get("price_period") != "month"
            or rental.get("rent_excludes_utilities") is not True
            or not isinstance(price, (int, float)) or isinstance(price, bool)
            or not math.isfinite(price) or price <= 0
            or rental_layout != layout or not rental_size or rental_size <= 0
            or abs(rental_size - size) / size > 0.25
            or not same_location
        ):
            continue
        seen_ids.add(identifier)
        comparables.append(rental)

    estimate["comparable_count"] = len(comparables)
    estimate["comparable_ids"] = [rental["id"] for rental in comparables]
    estimate["comparable_urls"] = [rental.get("seo_url") for rental in comparables]
    if len(comparables) < 3:
        return estimate
    rates = [rental["price"] / extract_metrics(rental["name"])[1] for rental in comparables]
    monthly = round(median(rates) * size)
    purchase_price = property_meta.get("price")
    valid_purchase_price = (
        isinstance(purchase_price, (int, float)) and not isinstance(purchase_price, bool)
        and math.isfinite(purchase_price) and purchase_price > 0
    )
    estimate.update({
        "status": "estimated",
        "monthly_rent_czk": monthly,
        "annual_rent_czk": monthly * 12,
        "gross_annual_yield_pct": round(monthly * 12 / purchase_price * 100, 2) if valid_purchase_price else None,
        "monthly_rent_range_czk": [round(min(rates) * size), round(max(rates) * size)],
    })
    return estimate

def extract_metrics(name_string):
    """
    Uses regex to pull layout and meter size from standard Sreality string titles.
    Example: "Prodej bytu 2+kk 58 m²" -> ("2+kk", 58.0)
    """
    # Regex to find layout formats like 2+kk, 3+1, 1+kk, etc.
    layout_match = re.search(r'(\d\+(?:kk|1))', name_string, re.IGNORECASE)
    
    # Regex to find the numeric square meters before 'm²'
    size_match = re.search(r'(\d+)\s*m²', name_string)
    
    layout = layout_match.group(1).lower() if layout_match else "atypical"
    size = float(size_match.group(1)) if size_match else None
    
    return layout, size

def build_market_baseline(listings):
    """
    Aggregates statistical calculations grouped by property layout.
    """
    metrics_by_layout = defaultdict(list)
    
    for item in listings:
        price = item.get("price")
        # Ensure price is an integer and valid (exclude "Cena na vyžádání" / price on request)
        if not price or not isinstance(price, (int, float)) or price <= 0:
            continue
            
        layout, size = extract_metrics(item.get("name", ""))
        
        if size and size > 0:
            price_per_m2 = price / size
            metrics_by_layout[layout].append({
                "price_per_m2": price_per_m2,
                "price": price,
                "size": size,
                "raw_data": item
            })
            
    # Compute averages per layout group
    baselines = {}
    for layout, data_list in metrics_by_layout.items():
        total_m2_price = sum(x["price_per_m2"] for x in data_list)
        count = len(data_list)
        avg_m2_price = total_m2_price / count
        
        baselines[layout] = {
            "avg_price_per_m2": avg_m2_price,
            "sample_count": count
        }
        
    return metrics_by_layout, baselines

def triage_listings(metrics_by_layout, baselines, target_discount=0.05):
    """
    Flags properties that fall below market average as primary AI candidates.
    target_discount = 0.05 means any property at least 5% cheaper than the local average.
    """
    candidates = []
    skipped_count = 0
    
    print("\n=== LOCAL BASINGS CALCULATED ===")
    for layout, stats in baselines.items():
        print(f"Layout [{layout.upper()}]: Avg Price/m² = {int(stats['avg_price_per_m2']):,} CZK ({stats['sample_count']} properties checked)")

    print("\n=== TRIAGE RUNNING ===")
    for layout, properties in metrics_by_layout.items():
        avg_m2_baseline = baselines[layout]["avg_price_per_m2"]
        
        for prop in properties:
            m2_price = prop["price_per_m2"]
            raw = prop["raw_data"]
            
            # Calculate the deviation from the statistical neighborhood norm
            # Negative deviation means it is cheaper than average
            deviation = (m2_price - avg_m2_baseline) / avg_m2_baseline
            
            evaluation_entry = {
                "id": raw.get("id"),
                "name": raw.get("name"),
                "locality": raw.get("locality"),
                "price": raw.get("price"),
                "size_m2": prop["size"],
                "price_per_m2": int(m2_price),
                "market_average_m2": int(avg_m2_baseline),
                "deviation_pct": round(deviation * 100, 2),
                "seo_url": raw.get("seo_url")
            }
            if raw.get("municipality_id") is not None:
                evaluation_entry["municipality_id"] = raw["municipality_id"]
            
            # Filter condition: It must be cheaper than the average market threshold
            if deviation <= -target_discount:
                print(f"[✔] CANDIDATE FLAGGED: {evaluation_entry['name']} in {evaluation_entry['locality']} is {abs(evaluation_entry['deviation_pct'])}% BELOW average.")
                candidates.append(evaluation_entry)
            else:
                skipped_count += 1
                
    print(f"\nTriage complete. Evaluated {len(candidates) + skipped_count} listings. Retained {len(candidates)} high-potential AI analysis candidates.")
    return candidates

# --- Execution Entry Point ---
if __name__ == "__main__":
    try:
        # Load the raw output generated in Step 1
        with open(RAW_OUTPUT_PATH, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        # 1. Group data and build layout averages
        grouped_metrics, local_baselines = build_market_baseline(raw_data)
        
        # 2. Extract potential good deals (e.g., at least 5% lower price per meter than baseline)
        ai_candidates = triage_listings(grouped_metrics, local_baselines, target_discount=0.05)

        if RENTAL_OUTPUT_PATH.exists():
            with open(RENTAL_OUTPUT_PATH, "r", encoding="utf-8") as f:
                rentals = json.load(f)
        else:
            print("[-] Rental data not found; rent estimates will be unavailable. Run the scraper first.")
            rentals = []
        for candidate in ai_candidates:
            candidate["rental_estimate"] = estimate_rental_income(candidate, rentals)
        
        # 3. Export candidates list to be consumed by Step 3 (The LLM Layer)
        CANDIDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CANDIDATES_PATH, "w", encoding="utf-8") as f:
            json.dump(ai_candidates, f, indent=4, ensure_ascii=False)
            
        print(f"[!] Exported candidates to '{CANDIDATES_PATH}'")
        
    except FileNotFoundError:
        print(f"[-] Error: '{RAW_OUTPUT_PATH}' not found. Run 'python -m sreality_investment_pipeline.scraper_harness' first.")
        raise SystemExit(1)
