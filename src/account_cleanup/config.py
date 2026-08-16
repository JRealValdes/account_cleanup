from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "data" / "raw"
DATA_INTERIM = ROOT / "data" / "interim"
DATA_PROCESSED = ROOT / "data" / "processed"

EMAILS_JSONL = DATA_INTERIM / "emails.jsonl"
CANDIDATES_JSON = DATA_INTERIM / "account_candidates.json"
INVENTORY_CSV = DATA_PROCESSED / "accounts_inventory.csv"
REVIEWED_JSON = ROOT / "data" / "reviewed.json"
REVIEW_MATCHES_JSON = DATA_INTERIM / "review_matches.json"

GOOGLE_EMAILS = {
    "javivireal": "javivireal@gmail.com",
    "jrealvaldes": "jrealvaldes@gmail.com",
}

DEFAULT_MODEL = "gpt-5.6-luna"

load_dotenv(ROOT / ".env")
