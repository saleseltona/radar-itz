"""
Execução real das consultas de busca do Radar (32 obrigatórias + derivadas + adicionais).

Cada consulta vira uma requisição ao Google News RSS (fonte aberta, sem chave), com:
- pausa entre requisições, para não sobrecarregar o serviço;
- orçamento de tempo total, para a varredura não segurar a requisição por tempo demais;
- parada imediata ao receber limite de requisições (HTTP 429/503), sem insistir;
- registro honesto do resultado de cada consulta: executada com itens, executada
  sem itens, com erro, interrompida por limite ou não executada por falta de tempo.

Nada aqui declara sucesso para uma consulta que não foi efetivamente feita.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.parse
from typing import Any, Callable, Dict, List, Optional

from src.config import mask_secrets

GOOGLE_NEWS_RSS_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
)

STATUS_COM_ITENS = "executada_com_itens"
STATUS_SEM_ITENS = "executada_sem_itens"
STATUS_ERRO = "erro"
STATUS_LIMITE = "interrompida_limite_requisicoes"
STATUS_SEM_TEMPO = "nao_executada_tempo_esgotado"

STATUS_EXECUTADA = (STATUS_COM_ITENS, STATUS_SEM_ITENS)

ORCAMENTO_PADRAO_SEGUNDOS = 45.0

# HTTP que indicam limite de taxa / bloqueio temporário do serviço de busca
HTTP_LIMITE = (429, 503)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def build_google_news_url(consulta: str, janela: str = "14d") -> str:
    termo = urllib.parse.quote_plus(consulta.strip())
    if janela:
        termo = f"{termo}+when:{janela}"
    return GOOGLE_NEWS_RSS_TEMPLATE.format(query=termo)


class QueryRunner:
    """Executa uma lista de consultas via Google News RSS com limites e relatório por consulta."""

    def __init__(
        self,
        http_get: Callable[[str], str],
        parse_rss: Callable[[str, str], List[Dict[str, Any]]],
        *,
        delay_seconds: Optional[float] = None,
        budget_seconds: Optional[float] = None,
        janela: str = "14d",
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.http_get = http_get
        self.parse_rss = parse_rss
        self.delay_seconds = (
            _env_float("RADAR_QUERY_DELAY_SECONDS", 0.5) if delay_seconds is None else delay_seconds
        )
        self.budget_seconds = (
            _env_float("RADAR_QUERY_BUDGET_SECONDS", ORCAMENTO_PADRAO_SEGUNDOS) if budget_seconds is None else budget_seconds
        )
        self.janela = janela
        self.clock = clock
        self.sleep = sleep

    def run(self, consultas: List[str]) -> Dict[str, Any]:
        # Remove vazias e repetidas mantendo a ordem (obrigatórias primeiro)
        vistas: set = set()
        lista: List[str] = []
        for c in consultas:
            termo = str(c or "").strip()
            if termo and termo.lower() not in vistas:
                vistas.add(termo.lower())
                lista.append(termo)

        relatorio: List[Dict[str, Any]] = []
        itens: List[Dict[str, Any]] = []
        inicio = self.clock()
        motivo_parada = ""

        for idx, consulta in enumerate(lista):
            url = build_google_news_url(consulta, self.janela)
            entrada: Dict[str, Any] = {"consulta": consulta, "url": url, "itens": 0, "erro": ""}

            if motivo_parada:
                entrada["status"] = STATUS_LIMITE if motivo_parada == "limite" else STATUS_SEM_TEMPO
                relatorio.append(entrada)
                continue

            if idx > 0 and self.delay_seconds > 0 and self.clock() - inicio < self.budget_seconds:
                self.sleep(self.delay_seconds)

            if self.clock() - inicio >= self.budget_seconds:
                motivo_parada = "tempo"
                entrada["status"] = STATUS_SEM_TEMPO
                relatorio.append(entrada)
                continue

            try:
                xml_text = self.http_get(url)
            except urllib.error.HTTPError as e:
                entrada["erro"] = mask_secrets(f"HTTP {e.code}: {e.reason}")
                if e.code in HTTP_LIMITE:
                    entrada["status"] = STATUS_LIMITE
                    motivo_parada = "limite"
                else:
                    entrada["status"] = STATUS_ERRO
                relatorio.append(entrada)
                continue
            except Exception as e:  # rede, DNS, timeout
                entrada["status"] = STATUS_ERRO
                entrada["erro"] = mask_secrets(f"{type(e).__name__}: {e}")
                relatorio.append(entrada)
                continue

            encontrados = self.parse_rss(xml_text, consulta)
            entrada["itens"] = len(encontrados)
            entrada["status"] = STATUS_COM_ITENS if encontrados else STATUS_SEM_ITENS
            itens.extend(encontrados)
            relatorio.append(entrada)

        contagem: Dict[str, int] = {}
        for r in relatorio:
            contagem[r["status"]] = contagem.get(r["status"], 0) + 1

        return {
            "itens": itens,
            "relatorio": relatorio,
            "total_previstas": len(lista),
            "total_executadas": sum(contagem.get(s, 0) for s in STATUS_EXECUTADA),
            "contagem_por_status": contagem,
            "duracao_segundos": round(self.clock() - inicio, 2),
        }


def resumo_cobertura(resultado: Dict[str, Any]) -> str:
    """Frase curta e honesta sobre quantas consultas rodaram de fato."""
    total = resultado.get("total_previstas", 0)
    feitas = resultado.get("total_executadas", 0)
    c = resultado.get("contagem_por_status", {})
    partes = [f"{feitas} de {total} consultas de busca executadas no Google News RSS"]
    if c.get(STATUS_COM_ITENS):
        partes.append(f"{c[STATUS_COM_ITENS]} com resultados sobre Imperatriz")
    if c.get(STATUS_ERRO):
        partes.append(f"{c[STATUS_ERRO]} com erro")
    if c.get(STATUS_LIMITE):
        partes.append(f"{c[STATUS_LIMITE]} interrompidas por limite de requisições")
    if c.get(STATUS_SEM_TEMPO):
        partes.append(f"{c[STATUS_SEM_TEMPO]} não executadas por falta de tempo")
    return "; ".join(partes) + "."
