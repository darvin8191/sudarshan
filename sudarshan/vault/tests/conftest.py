"""Vault tests run on throwaway files with a fake pepper; an operator's .env is never read."""

from __future__ import annotations

import os
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-vault-tests-")
os.environ.setdefault("API_KEY_PEPPER", "f" * 64)  # fake, test only
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/openalgo-test.db"
os.environ["LOG_DIR"] = f"{_TMP}/log"

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv
