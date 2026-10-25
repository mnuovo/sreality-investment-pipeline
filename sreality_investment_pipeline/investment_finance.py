import math


def calculate_investment_scenarios(property_meta):
    assumptions = property_meta.get("investment_assumptions") or {}

    def nonnegative(value):
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None

    price = nonnegative(property_meta.get("price"))
    price = price if price else None
    rental = property_meta.get("rental_estimate") or {}
    rent = nonnegative(rental.get("monthly_rent_czk")) if rental.get("status") == "estimated" else None
    legal = nonnegative(assumptions.get("legal_costs_czk"))
    repairs = nonnegative(assumptions.get("initial_repairs_czk"))
    other_entry = nonnegative(assumptions.get("other_entry_costs_czk", 0))
    hoa = nonnegative(assumptions.get("monthly_hoa_czk"))
    insurance = nonnegative(assumptions.get("monthly_insurance_czk"))
    other_monthly = nonnegative(assumptions.get("other_monthly_costs_czk", 0))
    entry = price + legal + repairs + other_entry if all(value is not None for value in (price, legal, repairs, other_entry)) else None
    annual = rent * 12 if rent is not None else None
    net = annual * 0.95 - 12 * (hoa + insurance + other_monthly) if all(value is not None for value in (annual, hoa, insurance, other_monthly)) else None
    net_yield = net / entry * 100 if net is not None and entry else None
    missing = [name for name, value in (
        ("purchase_price_czk", price), ("supported_monthly_base_rent_czk", rent),
        ("legal_costs_czk", legal), ("initial_repairs_czk", repairs),
        ("other_entry_costs_czk", other_entry), ("monthly_hoa_czk", hoa),
        ("monthly_insurance_czk", insurance), ("other_monthly_costs_czk", other_monthly),
    ) if value is None]
    mortgages = []
    for rate_pct in (4.5, 5.0):
        principal = price * 0.8 if price else None
        monthly_rate = rate_pct / 1200
        payment = principal * monthly_rate / (1 - (1 + monthly_rate) ** -360) if principal else None
        cash_invested = entry - principal if entry is not None and principal is not None else None
        cash_flow = net - payment * 12 if net is not None and payment is not None else None
        mortgages.append({
            "annual_interest_rate_pct": rate_pct,
            "loan_czk": round(principal, 2) if principal is not None else None,
            "monthly_payment_czk": round(payment, 2) if payment is not None else None,
            "initial_cash_czk": round(cash_invested, 2) if cash_invested is not None else None,
            "annual_cash_flow_czk": round(cash_flow, 2) if cash_flow is not None else None,
            "cash_on_cash_return_pct": round(cash_flow / cash_invested * 100, 2) if cash_flow is not None and cash_invested else None,
        })
    return {
        "purchase_price_czk": price,
        "acquisition_tax_czk": 0,
        "acquisition_tax_note": "The Czech 4% real-estate acquisition tax was abolished in 2020; legal and other transaction fees are separate.",
        "legal_costs_czk": legal,
        "initial_repairs_czk": repairs,
        "other_entry_costs_czk": other_entry,
        "total_entry_cost_czk": round(entry, 2) if entry is not None else None,
        "monthly_hoa_czk": hoa,
        "monthly_insurance_czk": insurance,
        "other_monthly_costs_czk": other_monthly,
        "vacancy_reserve_pct": 5,
        "annual_gross_rent_czk": annual,
        "gross_rental_yield_pct": round(annual / price * 100, 2) if annual is not None and price else None,
        "annual_net_operating_income_czk": round(net, 2) if net is not None else None,
        "net_rental_yield_pct": round(net_yield, 2) if net_yield is not None else None,
        "all_cash_cash_on_cash_return_pct": round(net_yield, 2) if net_yield is not None else None,
        "mortgage_scenarios": mortgages,
        "missing_inputs": missing,
        "assumptions": [
            "Vacancy reserve: 5% of annual base rent; HOA/fond oprav and insurance are owner costs, not tenant utility charges.",
            "Unspecified other upfront and monthly costs default to zero; supply maintenance, property tax and other applicable costs explicitly.",
            "Mortgage scenarios are illustrative: 80% loan-to-value, 30-year amortization, 4.5% and 5.0% nominal annual interest held constant.",
            "These are not current bank offers or an eligibility assessment; bank fees and changing rates are not modeled.",
            "Net figures are before income tax and only cover the supplied costs. Cash-on-cash excludes appreciation, sale proceeds and mortgage principal gains; total holding-period ROI is not estimated.",
        ],
    }