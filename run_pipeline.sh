#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
cd "$PROJECT_ROOT"

trap 'printf "[-] Pipeline failed at line %s. No later stages were run.\n" "$LINENO" >&2' ERR

if [[ ! -x "$PYTHON" ]]; then
    printf '%s\n' '[-] Project virtual environment not found. Follow the setup steps in README.md.' >&2
    exit 1
fi

for argument in "$@"; do
    if [[ "$argument" == "--help" || "$argument" == "-h" ]]; then
        exec "$PYTHON" -m sreality_investment_pipeline.scraper_harness "$@"
    fi
done

printf '%s\n' '[*] Checking dependencies, Ollama, and the configured model...'
"$PYTHON" - "$@" <<'PY'
import sys

try:
    import ollama
    from sreality_investment_pipeline.ai_analyzer_agent import OLLAMA_MODEL
    from sreality_investment_pipeline.scraper_harness import build_argument_parser

    parser = build_argument_parser()
    args = parser.parse_args(sys.argv[1:])
    if args.setup_consent:
        parser.error("Run consent setup directly with 'python -m sreality_investment_pipeline.scraper_harness --setup-consent'.")

    ollama.Client(timeout=10).show(OLLAMA_MODEL)
except Exception as error:
    print(f"[-] Preflight failed: {error}", file=sys.stderr)
    print("Check project dependencies, start Ollama, and download the configured model. See README.md.", file=sys.stderr)
    sys.exit(1)
PY

printf '\n%s\n' '[1/4] Collecting sale and rental listings...'
"$PYTHON" -u -m sreality_investment_pipeline.scraper_harness "$@"

printf '\n%s\n' '[2/4] Calculating investment candidates and rental yields...'
"$PYTHON" -u -m sreality_investment_pipeline.math_filter_engine

printf '\n%s\n' '[3/4] Running local AI risk analysis...'
"$PYTHON" -u -m sreality_investment_pipeline.ai_analyzer_agent

printf '\n%s\n' '[4/4] Generating the investment report...'
"$PYTHON" -u -m sreality_investment_pipeline.generate_dashboard

printf '\n[+] Pipeline completed. Report: %s/reports/INVESTMENT_REPORT.md\n' "$PROJECT_ROOT"