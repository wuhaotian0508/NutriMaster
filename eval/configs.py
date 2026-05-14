# eval/configs.py
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import dotenv

dotenv.load_dotenv(".env.local")
dotenv.load_dotenv()
dotenv.load_dotenv(".eval_env", override=True)

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
QUESTION_DB_ID = os.getenv("QUESTION_DB_ID", "e755b041d920410fa6dd3aa88c421879")
RESULT_DB_ID = os.getenv("RESULT_DB_ID", "c7b1b42c0ac14b5f883725f75860860e")

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.gpugeek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
JUDGE_BASE_URL = os.getenv("JUDGE_BASE_URL", LLM_BASE_URL)
JUDGE_API_KEY = os.getenv("JUDGE_API_KEY", LLM_API_KEY)
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "Vendor2/Gemini-3.1-pro")

LLM_AGENTS = [
    {"id": "Vendor2/Claude-4.5-Sonnet", "short": "Claude-4.5-Sonnet"},
    {"id": "Vendor2/GPT-5.4", "short": "GPT-5.4"},
]

NUTRIMASTER_NAME = os.getenv("NUTRIMASTER_AGENT_NAME", "NutriMaster")
NUTRIMASTER_MODEL_ID = os.getenv("NUTRIMASTER_MODEL_ID", "")
NUTRIMASTER_USE_DEPTH = os.getenv("NUTRIMASTER_USE_DEPTH", "1").lower() in {"1", "true", "yes"}

EVOMASTER_PLAYGROUND = os.getenv("EVOMASTER_PLAYGROUND", "fs_mv")
EVOMASTER_CONFIG = os.getenv("EVOMASTER_CONFIG", "")
EVOMASTER_MODEL = os.getenv("EVOMASTER_MODEL") or os.getenv("MAIN_MODEL", "deepseek-v4-flash")
EVOMASTER_TIMEOUT = int(os.getenv("EVOMASTER_TIMEOUT", "3600"))

LOCAL_TIMEZONE = ZoneInfo(os.getenv("EVAL_TIMEZONE", "Asia/Shanghai"))

EVAL_DIR = Path(__file__).parent
QUESTIONS_DIR = EVAL_DIR / "data" / "questions"
RESULTS_DIR = EVAL_DIR / "data" / "results"
DEFAULT_QUESTIONS_FILE = QUESTIONS_DIR / "latest.jsonl"
LEGACY_QUESTIONS_FILE = EVAL_DIR / "data" / "questions.jsonl"
DEFAULT_RESULTS_FILE = RESULTS_DIR / "latest.jsonl"
