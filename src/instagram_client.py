"""
Cliente mínimo da Instagram Graph API (Business Discovery).

Lê as publicações recentes de perfis públicos do tipo Empresa ou Criador de conteúdo,
usando uma conta profissional própria (INSTAGRAM_BUSINESS_ACCOUNT_ID) e um token de
acesso da Meta (INSTAGRAM_GRAPH_TOKEN). Perfis pessoais não são acessíveis por esta API.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

# Versão usada na configuração do app "Radar de Pautas" (nov/2026). Pode ser trocada sem mexer no código.
GRAPH_API_VERSION = os.environ.get("INSTAGRAM_GRAPH_API_VERSION", "").strip() or "v27.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
MEDIA_FIELDS = "caption,permalink,timestamp,media_type,media_url,thumbnail_url"


class InstagramGraphError(Exception):
    """Falha ao consultar a Graph API (token inválido, perfil pessoal, limite de uso etc.)."""


# Códigos de erro da Graph API com orientação para o painel.
_DICAS_ERRO = {
    190: "token expirado ou revogado; gere um novo token de usuário (EAA...) e atualize INSTAGRAM_GRAPH_TOKEN",
    10: "o token não tem as permissões exigidas (instagram_basic, instagram_manage_insights, pages_read_engagement e, via portfólio, ads_read)",
    200: "o token não tem as permissões exigidas para esta consulta",
    4: "limite de chamadas da Graph API atingido; aguarde antes de nova coleta",
    17: "limite de chamadas da conta atingido; aguarde antes de nova coleta",
    32: "limite de chamadas da Página atingido; aguarde antes de nova coleta",
    110: "perfil não encontrado ou não é conta Empresa/Criador",
}


def _check_token(token: str) -> None:
    """Recusa tokens do 'Login do Instagram' (IGAA...), que não funcionam no Business Discovery."""
    if token.startswith("IG"):
        raise InstagramGraphError(
            "INSTAGRAM_GRAPH_TOKEN é um token do Login do Instagram (IGAA...), que só vale em "
            "graph.instagram.com e não tem Business Discovery; use um token de usuário do Facebook (EAA...)"
        )


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
    _check_token(token)
    fields = f"business_discovery.username({username}){{username,media.limit({int(limit)}){{{MEDIA_FIELDS}}}}}"
    query = urllib.parse.urlencode({"fields": fields, "access_token": token})
    url = f"{GRAPH_BASE_URL}/{urllib.parse.quote(business_account_id)}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as he:
        detalhe, codigo = "", None
        try:
            erro = json.loads(he.read().decode("utf-8")).get("error", {})
            detalhe, codigo = erro.get("message", ""), erro.get("code")
        except Exception:
            pass
        dica = _DICAS_ERRO.get(codigo)
        msg = f"HTTP {he.code} na Graph API: {detalhe or he.reason}"
        if dica:
            msg += f" ({dica})"
        raise InstagramGraphError(msg) from he
    except Exception as e:  # rede, JSON inválido
        raise InstagramGraphError(f"Falha de rede na Graph API: {e}") from e

    discovery = payload.get("business_discovery") or {}
    return list((discovery.get("media") or {}).get("data") or [])
