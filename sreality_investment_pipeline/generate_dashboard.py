import json

from .math_filter_engine import estimate_rental_income
from .investment_finance import calculate_investment_scenarios
from .paths import ANALYSIS_PATH, CANDIDATES_PATH, RENTAL_OUTPUT_PATH, REPORT_PATH

def load_analyzed_data():
    try:
        with open(ANALYSIS_PATH, "r", encoding="utf-8") as f:
            properties = json.load(f)
    except FileNotFoundError:
        properties = []
    if CANDIDATES_PATH.exists():
        with open(CANDIDATES_PATH, "r", encoding="utf-8") as f:
            candidates = json.load(f)
        previous = {property_meta["id"]: property_meta for property_meta in properties}
        properties = [{**previous.get(candidate["id"], {}), **candidate} for candidate in candidates]
        if any(not property_meta.get("ai_evaluation") for property_meta in properties):
            print("[*] Some current candidates have AI risk review pending.")
    if RENTAL_OUTPUT_PATH.exists():
        with open(RENTAL_OUTPUT_PATH, "r", encoding="utf-8") as f:
            rentals = json.load(f)
    else:
        rentals = []
    for property_meta in properties:
        property_meta["rental_estimate"] = estimate_rental_income(property_meta, rentals)
        property_meta["financial_scenarios"] = calculate_investment_scenarios(property_meta)
    return properties

def investment_status(property_meta):
    review = property_meta.get("ai_evaluation") or {}
    if not review:
        return "Pending"
    verdict = review.get("investment_verdict")
    if verdict not in ("Yes", "No", "Conditional"):
        return "Needs reanalysis"
    finance = property_meta.get("financial_scenarios") or calculate_investment_scenarios(property_meta)
    if verdict == "Yes" and (finance["missing_inputs"] or review.get("missing_information") or review.get("traps_detected")):
        return "Conditional"
    return verdict

def markdown_cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

def build_investment_section(properties):
    def amount(value, percent=False):
        if value is None:
            return "Unavailable"
        return f"{value:.2f}%" if percent else f"{value:,.2f} CZK"

    content = "\n## Detailed Investment Analyses\n\n"
    content += (
        "Listing claims and model interpretations are not independent legal, technical or market verification. "
        "Legacy risk screens need reanalysis with the expanded prompt. Stored narratives are not refreshed by report generation.\n\n"
    )
    for property_meta in properties:
        review = property_meta.get("ai_evaluation") or {}
        current = review.get("investment_verdict") in ("Yes", "No", "Conditional")
        finance = property_meta.get("financial_scenarios") or calculate_investment_scenarios(property_meta)
        content += f"### [{markdown_cell(property_meta['name'])}]({property_meta['seo_url']})\n\n"
        content += "#### 1. Property & Location Overview\n\n"
        content += markdown_cell(review.get("property_location_overview", "Expanded location analysis unavailable; rerun the analyzer.")) + "\n\n"
        content += "#### 2. Potential Rent & Market Demand\n\n"
        content += markdown_cell(review.get("potential_rent_market_demand", "Expanded rental-demand and short-term analysis unavailable; see rental comparables below.")) + "\n\n"
        content += "#### 3. Financial & ROI Calculation\n\n"
        content += markdown_cell(review.get("financial_roi_analysis", "Financial narrative unavailable; deterministic scenarios follow.")) + "\n\n"
        content += f"- Asking purchase price: {amount(finance['purchase_price_czk'])}\n"
        content += "- Acquisition tax: 0 CZK. The former Czech 4% acquisition tax was abolished in 2020; transaction fees are separate.\n"
        content += f"- Legal services: {amount(finance['legal_costs_czk'])}; initial repairs: {amount(finance['initial_repairs_czk'])}; other entry costs: {amount(finance['other_entry_costs_czk'])}\n"
        content += f"- Total entry cost: {amount(finance['total_entry_cost_czk'])}\n"
        content += f"- Monthly owner HOA/fond oprav: {amount(finance['monthly_hoa_czk'])}; insurance: {amount(finance['monthly_insurance_czk'])}; other owner costs: {amount(finance['other_monthly_costs_czk'])}\n"
        content += f"- Gross rental yield on asking price: {amount(finance['gross_rental_yield_pct'], True)}\n"
        content += f"- Annual net operating income after 5% vacancy reserve: {amount(finance['annual_net_operating_income_czk'])}\n"
        content += f"- Pre-income-tax net yield / all-cash cash-on-cash on total entry cost: {amount(finance['net_rental_yield_pct'], True)}\n\n"
        content += "Illustrative mortgage scenarios (80% LTV, 30-year amortization; not verified current bank offers):\n\n"
        content += "| Rate | Monthly Payment | Initial Cash | Annual Cash Flow | Cash-on-Cash Return |\n"
        content += "| ---: | ---: | ---: | ---: | ---: |\n"
        for scenario in finance["mortgage_scenarios"]:
            content += f"| {scenario['annual_interest_rate_pct']:.1f}% | {amount(scenario['monthly_payment_czk'])} | {amount(scenario['initial_cash_czk'])} | {amount(scenario['annual_cash_flow_czk'])} | {amount(scenario['cash_on_cash_return_pct'], True)} |\n"
        content += "\nMissing financial inputs: " + (", ".join(finance["missing_inputs"]) or "None for this simplified scenario") + ".\n\n"
        content += "\n".join(f"- {assumption}" for assumption in finance["assumptions"]) + "\n\n"
        content += "#### 4. Investment Verdict\n\n"
        content += f"**{investment_status(property_meta)}**\n\n"
        if current:
            content += markdown_cell(review.get("summary_verdict", "")) + "\n\n"
            for label, key in (("Top Risks", "top_risks"), ("Top Upside Potentials", "top_upside_potentials"), ("Required Verification", "missing_information")):
                content += f"**{label}**\n\n"
                items = review.get(key) or []
                content += "\n".join(f"- {markdown_cell(item)}" for item in items) if items else "No further evidence-supported items supplied."
                content += "\n\n"
        else:
            content += "No current four-part investment verdict is available. Rerun the analyzer; the old boolean risk screen is not investment approval.\n\n"
    return content

def build_rental_section(properties):
    content = "\n## Rental Income and Gross Annual Yield\n\n"
    content += (
        "Estimates use advertised monthly base rents, not signed leases. Utility/service charges are screened "
        "from listing text; inclusive or unclear prices are excluded. Verify terms before investing. "
        "Comparables require the same layout and municipality, floor area within 25%, and at least 3 unique listings. "
        "Monthly rent uses median rent per m2 scaled to the purchase property's area. "
        "The range is the minimum-to-maximum comparable rate scaled to that area, not a confidence interval. "
        "Condition, furnishings and lease terms are not adjusted.\n\n"
        "**Gross annual yield = monthly rent x 12 / asking purchase price x 100.** "
        "This is not net ROI: vacancy, maintenance, insurance, taxes, transaction costs and financing are excluded.\n\n"
    )
    content += "| Property | Monthly Rent (CZK) | Comparable Range (CZK/month) | Annual Rent (CZK) | Gross Annual Yield | Comparables | AI Risk Review |\n"
    content += "| :--- | ---: | :--- | ---: | ---: | ---: | :--- |\n"
    for property_meta in properties:
        estimate = property_meta.get("rental_estimate") or {}
        name = property_meta["name"].replace("|", "\\|").replace("\n", " ")
        monthly = annual = yield_text = range_text = "Unavailable"
        if estimate.get("status") == "estimated":
            monthly = f"{estimate['monthly_rent_czk']:,}"
            annual = f"{estimate['annual_rent_czk']:,}"
            lower, upper = estimate["monthly_rent_range_czk"]
            range_text = f"{lower:,} - {upper:,}"
            if estimate.get("gross_annual_yield_pct") is not None:
                yield_text = f"{estimate['gross_annual_yield_pct']:.2f}%"
        else:
            monthly = "Insufficient data"
        risk = investment_status(property_meta)
        content += f"| [{name}]({property_meta['seo_url']}) | {monthly} | {range_text} | {annual} | {yield_text} | {estimate.get('comparable_count', 0)} | {risk} |\n"
    content += "\n### Rental Comparable Sources\n\n"
    for property_meta in properties:
        estimate = property_meta.get("rental_estimate") or {}
        sources = [f"[{identifier}]({url})" for identifier, url in zip(
            estimate.get("comparable_ids", []), estimate.get("comparable_urls", [])
        ) if url]
        content += f"- Listing {property_meta['id']}: {', '.join(sources) if sources else 'No eligible rental comparables.'}\n"
    return content

def build_markdown_dashboard(properties):
    markdown_content = "# 🏰 Property Investment Dashboard\n\n"
    markdown_content += "This dashboard consolidates data from your custom AI harness, cross-referencing mathematical market deviations against LLM structural risk analysis.\n\n"
    if not properties:
        markdown_content += "No investment candidates were identified in the current search snapshot.\n\n"
    
    statuses = [investment_status(property_meta) for property_meta in properties]
    markdown_content += "## Pipeline Health Metrics\n"
    markdown_content += f"* **Current Investment Analyses:** {sum(status in ('Yes', 'No', 'Conditional') for status in statuses)}\n"
    markdown_content += f"* **AI Review Pending:** {statuses.count('Pending')}\n"
    markdown_content += f"* **Legacy Analyses Needing Rerun:** {statuses.count('Needs reanalysis')}\n"
    for verdict in ("Yes", "No", "Conditional"):
        markdown_content += f"* **{verdict} Verdicts:** {statuses.count(verdict)}\n"
    markdown_content += "\n## Candidate Investment Overview\n\n"
    markdown_content += "Model verdicts are preliminary decision support, not verified investment recommendations.\n\n"
    markdown_content += "| Property | Locality | Asking Price (CZK) | Discount | Verdict | Ownership Evidence | Summary |\n"
    markdown_content += "| :--- | :--- | ---: | ---: | :--- | :--- | :--- |\n"
    for property_meta in properties:
        review = property_meta.get("ai_evaluation") or {}
        current = review.get("investment_verdict") in ("Yes", "No", "Conditional")
        summary = review.get("summary_verdict", "") if current else "Expanded analysis required; legacy risk screen is not investment approval."
        markdown_content += f"| [{markdown_cell(property_meta['name'])}]({property_meta['seo_url']}) | {markdown_cell(property_meta['locality'])} | {property_meta['price']:,} | {property_meta['deviation_pct']}% | {investment_status(property_meta)} | {markdown_cell(review.get('ownership_confirmation', 'Unconfirmed'))} | {markdown_cell(summary)} |\n"
    markdown_content += build_investment_section(properties)
    markdown_content += build_rental_section(properties)

    # Export out to disk
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(markdown_content)
        
    print(f"[!] Success! Generated human-readable visual dashboard to '{REPORT_PATH}'")

if __name__ == "__main__":
    data = load_analyzed_data()
    if data is not None:
        build_markdown_dashboard(data)
