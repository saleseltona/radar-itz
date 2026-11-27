"""
Cliente mínimo da Instagram Graph API (Business Discovery).

Lê as publicações recentes de perfis públicos do tipo Empresa ou Criador de conteúdo,
usando uma conta profissional própria (INSTAGRAM_BUSINESS_ACCOUNT_ID) e um token de
acesso da Meta (INSTAGRAM_GRAPH_TOKEN). Perfis pessoais não são acessíveis por esta API.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

GRAPH_API_VERSION = "v19.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
MEDIA_FIELDS = "caption,permalink,timestamp,media_type,media_url,thumbnail_url"


class InstagramGraphError(Exception):
    """Falha ao consultar a Graph API (token inválido, perfil pessoal, limite de uso etc.)."""


def username_from_url(url: str) -> str:
    """Extrai o @ do perfil de uma URL como https://www.instagram.com/perfil/."""
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url or "")
    return m.group(1) if m else ""


def fetch_recent_media(
    username: str,
    business_account_id: str,
    token: str,
    limit: int = 10,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """Retorna as publicações mais recentes do perfil via Business Discovery."""
    if not username:
        raise InstagramGraphError("URL do perfil sem nome de usuário reconhecível")
    fields = f"business_discovery.username({username}){{username,media.limit({int(limit)}){{{MEDIA_FIELDS}}}}}"
    query = urllib.parse.urlencode({"fields": fields, "access_token": token})
    url = f"{GRAPH_BASE_URL}/{urllib.parse.quote(business_account_id)}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as he:
        detalhe = ""
        try:
            detalhe = json.loads(he.read().decode("utf-8")).get("error", {}).get("message", "")
        except Exception:
            pass
        raise InstagramGraphError(f"HTTP {he.code} na Graph API: {detalhe or he.reason}") from he
    except Exception as e:  # rede, JSON inválido
        raise InstagramGraphError(f"Falha de rede na Graph API: {e}") from e

    discovery = payload.get("business_discovery") or {}
    return list((discovery.get("media") or {}).get("data") or [])
