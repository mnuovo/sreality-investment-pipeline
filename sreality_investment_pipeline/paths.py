from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
STATE_DIR = PROJECT_ROOT / ".state"

DEFAULT_STATE_PATH = STATE_DIR / "sreality_browser_state.json"
RAW_OUTPUT_PATH = DATA_DIR / "sreality_raw_output.json"
RENTAL_OUTPUT_PATH = DATA_DIR / "sreality_rental_output.json"
CANDIDATES_PATH = DATA_DIR / "ai_pipeline_candidates.json"
ANALYSIS_PATH = DATA_DIR / "final_property_analysis.json"
REPORT_PATH = REPORTS_DIR / "INVESTMENT_REPORT.md"