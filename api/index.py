"""Entrypoint Serverless Python da Vercel para o Radar de Pautas de Imperatriz."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.web_server import RadarAPIHandler  # noqa: E402


class handler(RadarAPIHandler):
    """Handler HTTP padrão reconhecido pelo runtime Python da Vercel."""

    pass
