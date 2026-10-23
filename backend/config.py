"""Runtime configuration for DocOps Commander.

Everything has a safe default so the app runs locally with zero configuration.
The LLM layer is strictly optional: when no key is present the agent falls back
to a deterministic narrator that produces the same structured response.
"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_FILE = BASE_DIR / "data" / "sample_jobs.json"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = Path(os.getenv("DOCOPS_DB", str(BASE_DIR / "docops.db")))

# --- Optional LLM narration layer -------------------------------------------
# Any OpenAI-compatible endpoint works (OpenRouter, vLLM, Ollama, OpenAI).
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_MODEL = os.getenv("LLM_MODEL", "google/gemini-2.0-flash-001")
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "8"))
LLM_ENABLED = bool(LLM_API_KEY)

# --- Domain thresholds (also surfaced in the UI policy drawer) ---------------
AUTO_RETRY_VALUE_CEILING_USD = 25_000
HUMAN_SIGNOFF_VALUE_USD = 50_000
SLA_AT_RISK_HOURS = 12
SLA_CRITICAL_HOURS = 4
MAX_AUTO_ATTEMPTS = 3

SIMULATION_RETRY_RECOVERY_SECONDS = 3.0
