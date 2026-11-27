"""
Servidor Web e API RESTful nativa (sem dependências externas) para o Radar de Pautas de Imperatriz.
Disponibiliza um Dashboard Jornalístico interativo com busca, filtros por categoria,
visualização de evidências, disparo de novos ciclos de varredura e download de relatórios.
"""
from __future__ import annotations

import base64
import csv
import hmac
import http.cookies
import io
import json
import logging
import mimetypes
import os
import re
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from main import run_radar_cycle
from src.auto_refresh import (
    AutoAtualizador,
    TarefaOcupada,
    auto_atualizacao_habilitada,
)
from src.config import (
    ANA_ESTACAO_IMPERATRIZ_CODIGO,
    BASE_DIR,
    DB_PATH,
    FIORILLI_DEFAULT_YEARS,
    FONTES_CSV_PATH,
    IS_RENDER,
    OUTPUT_DIR,
    ensure_runtime_files,
    is_supabase_configured,
    mask_secrets,
)
from src.database import RadarDatabase, get_database
from src.fiorilli_client import FiorilliClient
from src.pncp_client import PncpClient
from src.pncp_sync import CursorInvalido, SyncEmAndamento, executar_lote
from src.source_manager import SourceManager
from src.transparencia_cross import (
    build_auditoria_dispensas,
    build_cruzamento_fornecedores,
    build_transparencia_resumo,
)

logger = logging.getLogger("radar_imperatriz.web")

# Anos de contratos PNCP lidos para o cruzamento (inclui exercícios anteriores ainda vigentes)
from src.transparencia_cross import PNCP_ANOS_CRUZAMENTO  # noqa: E402

STATIC_DIR = BASE_DIR / "static"

# Autorização das rotas que gravam dados.
# Com RADAR_ADMIN_TOKEN definido, exige o header X-Radar-Token (ou Authorization: Bearer).
# Sem o token, só aceita acesso local direto (127.0.0.1 / ::1, sem proxy na frente).
ADMIN_TOKEN_ENV = "RADAR_ADMIN_TOKEN"
_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
_LOOPBACK_HOSTNAMES = {"localhost", "127.0.0.1", "::1"}
_PROXY_HEADERS = (
    "X-Forwarded-For",
    "X-Forwarded-Host",
    "X-Real-IP",
    "Forwarded",
    "X-Vercel-Forwarded-For",
    "CF-Connecting-IP",
)


def resolve_static_file(rel_name: str) -> Optional[Path]:
    """Resolve um arquivo dentro de STATIC_DIR, recusando qualquer caminho que saia da pasta."""
    static_root = STATIC_DIR.resolve()
    try:
        candidate = (static_root / rel_name).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    try:
        candidate.relative_to(static_root)
    except ValueError:
        return None
    try:
        return candidate if candidate.is_file() else None
    except (OSError, ValueError):
        return None


def parse_brl_number(raw: Any) -> Optional[float]:
    """Converte valor monetário PT-BR ('R$ 1.682.632,75') ou decimal ('1682632.75') para float positivo."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
        return round(val, 2) if (val > 0 and val == val and abs(val) < 1e15) else None
    txt = str(raw or "").strip()
    if not txt:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", txt)
    if not cleaned or cleaned in ("-", ".", ","):
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        val = float(cleaned)
    except ValueError:
        return None
    if val <= 0 or val != val or abs(val) >= 1e15:
        return None
    return round(val, 2)


def get_dashboard_html() -> str:
    """Carrega o Dashboard Stitch oficial se disponível em static/stitch_dashboard.html, ou fallback."""
    stitch_path = STATIC_DIR / "stitch_dashboard.html"
    if stitch_path.exists():
        return stitch_path.read_text(encoding="utf-8")
    return _get_fallback_dashboard_html()


def _get_fallback_dashboard_html() -> str:
    """HTML5 moderno de contingência com Tailwind CSS e Lucide Icons."""
    return """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Radar de Pautas de Imperatriz (MA) | Central Editorial</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
    body { font-family: 'Inter', sans-serif; }
    .badge-emergencia { background: #fee2e2; color: #991b1b; border: 1px solid #f87171; }
    .badge-alta { background: #ffedd5; color: #9a3412; border: 1px solid #fb923c; }
    .badge-media { background: #fef9c3; color: #854d0e; border: 1px solid #facc15; }
    .badge-baixa { background: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; }
  </style>
</head>
<body class="bg-slate-50 text-slate-900 min-h-screen">
  <!-- Topbar -->
  <header class="bg-slate-900 text-white sticky top-0 z-40 border-b border-slate-800 shadow-md">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
      <div class="flex items-center space-x-3">
        <div class="w-10 h-10 rounded-lg bg-red-600 flex items-center justify-center font-bold text-xl shadow-inner">
          <i class="fa-solid fa-satellite-dish text-white"></i>
        </div>
        <div>
          <h1 class="text-lg font-bold tracking-tight">Radar de Pautas de Imperatriz</h1>
          <p class="text-xs text-slate-400">Inteligência Editorial, Investigação e Monitoramento Público</p>
        </div>
      </div>
      <div class="flex items-center space-x-3">
        <a href="/api/export/csv" class="px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-medium rounded-lg border border-slate-700 transition flex items-center gap-2">
          <i class="fa-solid fa-file-csv text-emerald-400"></i> Exportar CSV
        </a>
        <button id="btn-scan" onclick="triggerScan()" class="px-4 py-2 bg-red-600 hover:bg-red-700 text-white text-xs font-semibold rounded-lg shadow-sm transition flex items-center gap-2">
          <i id="scan-icon" class="fa-solid fa-arrows-rotate"></i>
          <span>Rastrear Agora</span>
        </button>
      </div>
    </div>
  </header>

  <!-- Container Principal -->
  <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
    <!-- Métricas -->
    <section class="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
      <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center space-x-4">
        <div class="w-12 h-12 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center text-xl">
          <i class="fa-solid fa-newspaper"></i>
        </div>
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Total de Pautas</p>
          <p id="stat-total" class="text-2xl font-bold text-slate-900">-</p>
        </div>
      </div>
      <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center space-x-4">
        <div class="w-12 h-12 rounded-lg bg-red-50 text-red-600 flex items-center justify-center text-xl">
          <i class="fa-solid fa-triangle-exclamation"></i>
        </div>
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Emergências</p>
          <p id="stat-emergencias" class="text-2xl font-bold text-slate-900">-</p>
        </div>
      </div>
      <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center space-x-4">
        <div class="w-12 h-12 rounded-lg bg-amber-50 text-amber-600 flex items-center justify-center text-xl">
          <i class="fa-solid fa-magnifying-glass-chart"></i>
        </div>
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Em Apuração</p>
          <p id="stat-pendencias" class="text-2xl font-bold text-slate-900">-</p>
        </div>
      </div>
      <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex items-center space-x-4">
        <div class="w-12 h-12 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center text-xl">
          <i class="fa-solid fa-circle-check"></i>
        </div>
        <div>
          <p class="text-xs font-medium text-slate-500 uppercase tracking-wider">Fontes Monitoradas</p>
          <p id="stat-fontes" class="text-2xl font-bold text-slate-900">-</p>
        </div>
      </div>
    </section>

    <!-- Barra de Ferramentas / Filtros -->
    <section class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm mb-6 flex flex-col md:flex-row gap-4 justify-between items-center">
      <div class="relative w-full md:w-96">
        <i class="fa-solid fa-search absolute left-3 top-3.5 text-slate-400 text-sm"></i>
        <input type="text" id="input-search" oninput="filterPautas()" placeholder="Buscar pauta, bairro, contrato ou órgão..." class="w-full pl-9 pr-4 py-2 text-sm bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-red-500 focus:bg-white transition">
      </div>
      <div class="flex items-center gap-2 overflow-x-auto w-full md:w-auto pb-2 md:pb-0">
        <button onclick="setCategoryFilter('todas')" class="cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-900 text-white" data-cat="todas">Todas</button>
        <button onclick="setCategoryFilter('Pautas urgentes')" class="cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200" data-cat="Pautas urgentes">🚨 Urgentes</button>
        <button onclick="setCategoryFilter('Pautas de serviço público')" class="cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200" data-cat="Pautas de serviço público">🏛️ Serviço Público</button>
        <button onclick="setCategoryFilter('Pautas de denúncia e apuração')" class="cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200" data-cat="Pautas de denúncia e apuração">🔍 Denúncia</button>
        <button onclick="setCategoryFilter('Pautas de cultura, economia e cidade')" class="cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200" data-cat="Pautas de cultura, economia e cidade">🎭 Cultura & Cidade</button>
      </div>
    </section>

    <!-- Lista de Pautas -->
    <section>
      <div class="flex items-center justify-between mb-4">
        <h2 class="text-base font-bold text-slate-800 flex items-center gap-2">
          <span>Feed de Pautas Identificadas</span>
          <span id="pautas-count-badge" class="px-2 py-0.5 text-xs bg-slate-200 text-slate-700 rounded-full font-semibold">0</span>
        </h2>
        <span class="text-xs text-slate-500">Ordenado por Índice de Prioridade Editorial</span>
      </div>

      <div id="pautas-grid" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <!-- Renderizado dinamicamente via JS -->
      </div>
    </section>
  </main>

  <!-- Modal de Detalhes -->
  <div id="modal-details" class="fixed inset-0 bg-slate-950/50 backdrop-blur-sm z-50 hidden flex items-center justify-center p-4">
    <div class="bg-white rounded-2xl max-w-2xl w-full max-h-[90vh] overflow-y-auto shadow-2xl border border-slate-200 p-6">
      <div class="flex items-start justify-between border-b border-slate-100 pb-4 mb-4">
        <div>
          <span id="modal-prio-badge" class="px-2.5 py-1 text-xs font-bold rounded-md"></span>
          <h3 id="modal-title" class="text-lg font-bold text-slate-900 mt-2"></h3>
        </div>
        <button onclick="closeModal()" class="text-slate-400 hover:text-slate-600 text-lg p-1">
          <i class="fa-solid fa-xmark"></i>
        </button>
      </div>
      <div class="space-y-4 text-sm text-slate-700">
        <div>
          <h4 class="text-xs font-bold uppercase text-slate-400 tracking-wider">Resumo Editorial</h4>
          <p id="modal-resumo" class="mt-1 text-slate-800 leading-relaxed"></p>
        </div>
        <div class="grid grid-cols-2 gap-3 bg-slate-50 p-3 rounded-lg border border-slate-100 text-xs">
          <div><span class="font-semibold text-slate-500">Fonte Principal:</span> <span id="modal-fonte" class="text-slate-900"></span></div>
          <div><span class="font-semibold text-slate-500">Nível de Verificação:</span> <span id="modal-nivel" class="font-bold"></span></div>
          <div><span class="font-semibold text-slate-500">Data Publicação:</span> <span id="modal-data-pub"></span></div>
          <div><span class="font-semibold text-slate-500">Data Fato:</span> <span id="modal-data-fato"></span></div>
          <div class="col-span-2"><span class="font-semibold text-slate-500">Locais / Bairros:</span> <span id="modal-locais"></span></div>
        </div>
        <div>
          <h4 class="text-xs font-bold uppercase text-slate-400 tracking-wider">Evidências e Documentos</h4>
          <p id="modal-evidencias" class="mt-1 text-xs text-slate-600 leading-relaxed font-mono bg-slate-50 p-3 rounded-lg border border-slate-100"></p>
        </div>
        <div>
          <h4 class="text-xs font-bold uppercase text-slate-400 tracking-wider">Ângulo e Pauta Recomendada</h4>
          <p id="modal-angulo" class="mt-1 text-slate-800"></p>
        </div>
        <div class="pt-4 border-t border-slate-100 flex items-center justify-between">
          <a id="modal-link-original" href="#" target="_blank" class="text-xs font-semibold text-blue-600 hover:underline flex items-center gap-1">
            <span>Acessar Link Original</span> <i class="fa-solid fa-arrow-up-right-from-square"></i>
          </a>
          <button onclick="closeModal()" class="px-4 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold rounded-lg transition">Fechar</button>
        </div>
      </div>
    </div>
  </div>

  <script src="/static/radar_auth.js"></script>
  <script>
    let pautasData = [];
    let currentCategory = 'todas';

    async function loadData() {
      try {
        const res = await fetch('/api/pautas');
        pautasData = await res.json();
        updateStats();
        filterPautas();
      } catch (err) {
        console.error("Erro ao carregar pautas:", err);
      }
    }

    function updateStats() {
      document.getElementById('stat-total').innerText = pautasData.length;
      document.getElementById('stat-emergencias').innerText = pautasData.filter(p => p.prioridade === 'Emergência').length;
      document.getElementById('stat-pendencias').innerText = pautasData.filter(p => p.nivel_verificacao !== 'Confirmado').length;
      document.getElementById('stat-fontes').innerText = '22';
    }

    function setCategoryFilter(cat) {
      currentCategory = cat;
      document.querySelectorAll('.cat-btn').forEach(b => {
        if (b.dataset.cat === cat) {
          b.className = 'cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-900 text-white';
        } else {
          b.className = 'cat-btn px-3 py-1.5 text-xs font-semibold rounded-lg bg-slate-100 text-slate-600 hover:bg-slate-200';
        }
      });
      filterPautas();
    }

    function filterPautas() {
      const q = document.getElementById('input-search').value.toLowerCase().trim();
      const filtered = pautasData.filter(p => {
        const matchCat = (currentCategory === 'todas' || p.categoria === currentCategory);
        const matchQ = (!q || 
          p.titulo_jornalistico.toLowerCase().includes(q) ||
          p.resumo_3_linhas.toLowerCase().includes(q) ||
          p.bairros_locais_instituicoes.toLowerCase().includes(q) ||
          p.fonte_principal.toLowerCase().includes(q)
        );
        return matchCat && matchQ;
      });

      renderGrid(filtered);
    }

    function renderGrid(list) {
      const grid = document.getElementById('pautas-grid');
      document.getElementById('pautas-count-badge').innerText = list.length;
      grid.innerHTML = '';

      if (list.length === 0) {
        grid.innerHTML = '<div class="col-span-2 py-12 text-center text-slate-400 text-sm">Nenhuma pauta encontrada com os filtros selecionados.</div>';
        return;
      }

      list.forEach(p => {
        let badgeClass = 'badge-baixa';
        if (p.prioridade === 'Emergência') badgeClass = 'badge-emergencia';
        else if (p.prioridade === 'Alta') badgeClass = 'badge-alta';
        else if (p.prioridade === 'Média') badgeClass = 'badge-media';

        const card = document.createElement('div');
        card.className = 'bg-white p-5 rounded-xl border border-slate-200 shadow-sm hover:shadow-md transition flex flex-col justify-between';
        card.innerHTML = `
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="px-2 py-0.5 rounded text-[11px] font-bold ${badgeClass}">
                ${p.prioridade === 'Emergência' ? '🚨 EMERGÊNCIA' : p.prioridade + ' (' + p.pontuacao_editorial + ' pts)'}
              </span>
              <span class="text-[11px] text-slate-400 font-medium">${p.categoria}</span>
            </div>
            <h3 class="text-sm font-bold text-slate-900 leading-snug hover:text-red-600 transition cursor-pointer" onclick='openModal(${JSON.stringify(p.id_pauta)})'>
              ${p.titulo_jornalistico}
            </h3>
            <p class="text-xs text-slate-600 mt-2 line-clamp-3 leading-relaxed">
              ${p.resumo_3_linhas}
            </p>
          </div>
          <div class="mt-4 pt-3 border-t border-slate-100 flex items-center justify-between text-[11px] text-slate-500">
            <span class="truncate max-w-[180px] font-medium text-slate-700">
              <i class="fa-solid fa-link text-slate-400 mr-1"></i> ${p.fonte_principal}
            </span>
            <div class="flex items-center gap-2">
              <span class="px-2 py-0.5 rounded bg-slate-100 text-slate-600 font-semibold">${p.formato_sugerido}</span>
              <button onclick='openModal(${JSON.stringify(p.id_pauta)})' class="text-red-600 hover:text-red-700 font-bold">Ver <i class="fa-solid fa-angle-right"></i></button>
            </div>
          </div>
        `;
        grid.appendChild(card);
      });
    }

    function openModal(idPauta) {
      const p = pautasData.find(x => x.id_pauta === idPauta);
      if (!p) return;

      document.getElementById('modal-title').innerText = p.titulo_jornalistico;
      document.getElementById('modal-resumo').innerText = p.resumo_3_linhas;
      document.getElementById('modal-fonte').innerText = p.fonte_principal + ' (' + p.veiculo_instituicao + ')';
      document.getElementById('modal-nivel').innerText = p.nivel_verificacao;
      document.getElementById('modal-data-pub').innerText = p.data_publicacao || 'N/D';
      document.getElementById('modal-data-fato').innerText = p.data_acontecimento || 'N/D';
      document.getElementById('modal-locais').innerText = p.bairros_locais_instituicoes || 'Imperatriz (MA)';
      document.getElementById('modal-evidencias').innerText = p.evidencias_verificadas;
      document.getElementById('modal-angulo').innerText = p.angulo_jornalistico;
      document.getElementById('modal-link-original').href = p.url_original;

      const badge = document.getElementById('modal-prio-badge');
      badge.innerText = p.prioridade === 'Emergência' ? '🚨 PRIORIDADE MÁXIMA / EMERGÊNCIA' : p.prioridade + ' (' + p.pontuacao_editorial + '/100 pts)';
      badge.className = 'px-2.5 py-1 text-xs font-bold rounded-md ' + (p.prioridade === 'Emergência' ? 'badge-emergencia' : (p.prioridade === 'Alta' ? 'badge-alta' : 'badge-media'));

      document.getElementById('modal-details').classList.remove('hidden');
    }

    function closeModal() {
      document.getElementById('modal-details').classList.add('hidden');
    }

    async function triggerScan() {
      const btn = document.getElementById('btn-scan');
      const icon = document.getElementById('scan-icon');
      btn.disabled = true;
      icon.classList.add('fa-spin');
      btn.classList.add('opacity-75');

      try {
        const res = await fetch('/api/executar', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const result = await res.json();
        await loadData();
        alert('Ciclo concluído com sucesso! Pautas novas: ' + result.pautas_novas);
      } catch (err) {
        alert('Erro ao executar o radar: ' + err);
      } finally {
        btn.disabled = false;
        icon.classList.remove('fa-spin');
        btn.classList.remove('opacity-75');
      }
    }

    document.addEventListener('DOMContentLoaded', loadData);
  </script>
</body>
</html>
"""


def pncp_cobertura_registrada(db: Any) -> Optional[Dict[str, Any]]:
    """Lê do banco se a sincronização PNCP percorreu todo o período (carga completa).

    Usa o estado persistido pela sincronização em lotes (`get_pncp_sync_state`), quando o
    backend o oferece. Sem esse registro devolve None, e o cruzamento trata ausências como
    inconclusivas em vez de afirmar que o contrato não está no PNCP.
    """
    getter = getattr(db, "get_pncp_sync_state", None)
    if not callable(getter):
        return None
    try:
        estado = getter() or {}
    except Exception as exc:  # estado indisponível não pode virar "cobertura completa"
        logger.warning("Estado da sincronização PNCP indisponível: %s", exc)
        return None
    if not isinstance(estado, dict) or not estado:
        return None
    carga_ok = bool(estado.get("carga_completa_concluida"))
    pendente = estado.get("cursor_pendente")
    carga_pendente = isinstance(pendente, dict) and pendente.get("modo") == "carga_completa"
    completa = carga_ok and not carga_pendente
    motivo = ""
    if not carga_ok:
        motivo = "carga completa do PNCP ainda não concluída"
    elif carga_pendente:
        motivo = "nova carga completa do PNCP em andamento"
    return {
        "completa": completa,
        "truncada": False,
        "anos": list(estado.get("carga_completa_anos") or []),
        "motivo": motivo,
        "ultima_data_sincronizada": str(estado.get("ultima_data_sincronizada") or ""),
    }


class RadarAPIHandler(BaseHTTPRequestHandler):
    """Manipulador HTTP REST e servidor estático embutido (local, Render e Docker)."""

    _db: Optional[RadarDatabase] = None

    @classmethod
    def get_db(cls) -> Any:
        """Seleciona Supabase (quando configurado) ou inicializa SQLite local sob demanda."""
        if is_supabase_configured():
            if cls._db is None or getattr(cls._db, "backend_name", "") != "supabase":
                cls._db = get_database(DB_PATH)
            return cls._db

        ensure_runtime_files()
        if (
            cls._db is None
            or getattr(cls._db, "backend_name", "") != "sqlite"
            or cls._db.db_path != DB_PATH
            or not DB_PATH.exists()
        ):
            cls._db = RadarDatabase(DB_PATH)
        return cls._db

    def _decode_base64_json_header(
        self, header_name: str, cookie_name: str
    ) -> Optional[Dict[str, Any]]:
        """Decodifica payload JSON Base64 UTF-8 enviado por cabeçalho HTTP ou fallback em cookie."""
        raw_b64 = str(self.headers.get(header_name) or "").strip()
        if not raw_b64:
            cookie_header = str(self.headers.get("Cookie") or "").strip()
            if cookie_header:
                try:
                    jar = http.cookies.SimpleCookie()
                    jar.load(cookie_header)
                    morsel = jar.get(cookie_name)
                    if morsel and morsel.value:
                        raw_b64 = urllib.parse.unquote(morsel.value).strip()
                except Exception:
                    raw_b64 = ""
        if not raw_b64 or len(raw_b64) > 32_768:
            return None
        try:
            padded = raw_b64 + ("=" * (-len(raw_b64) % 4))
            decoded = base64.b64decode(padded).decode("utf-8", errors="strict")
            parsed = json.loads(decoded)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _apply_request_pautas_arquivadas(self, db: Any) -> int:
        """Sincroniza pautas arquivadas via header/cookie no modo SQLite stateless."""
        if getattr(db, "backend_name", "sqlite") != "sqlite":
            return 0
        parsed = self._decode_base64_json_header("X-Pautas-Arquivadas", "radar_pautas_arq")
        if not parsed:
            return 0
        normalized: Dict[str, Any] = {}
        for raw_id, entry in list(parsed.items())[:100]:
            pid = str(raw_id or "").strip()
            if not pid or not isinstance(entry, dict):
                continue
            if entry.get("a") or entry.get("arquivar"):
                motivo = (
                    str(entry.get("m") or entry.get("motivo") or "Arquivada pela redação").strip()[:160]
                    or "Arquivada pela redação"
                )
                normalized[pid] = {"a": 1, "m": motivo}
            elif entry.get("u") or entry.get("restaurar"):
                normalized[pid] = {"u": 1}
        if not normalized:
            return 0
        return int(db.sync_pautas_arquivadas_from_request(normalized) or 0)

    def _apply_request_pncp_retificacoes(self, db: Any) -> int:
        """Sincroniza retificações via header/cookie no modo SQLite stateless (ignorado no Supabase)."""
        if getattr(db, "backend_name", "sqlite") != "sqlite":
            return 0
        parsed = self._decode_base64_json_header("X-PNCP-Retificacoes", "radar_pncp_retif")
        if not parsed:
            return 0

        normalized: Dict[str, Any] = {}
        for raw_ctrl, entry in list(parsed.items())[:100]:
            ctrl = str(raw_ctrl or "").strip()
            if not ctrl or not isinstance(entry, dict):
                continue
            item_norm: Dict[str, Any] = {}
            if entry.get("i") or entry.get("ignorar"):
                item_norm["i"] = 1
                item_norm["m"] = (
                    str(
                        entry.get("m")
                        or entry.get("motivo")
                        or entry.get("motivo_ignorado")
                        or "Contrato duplicado no PNCP"
                    ).strip()[:160]
                    or "Contrato duplicado no PNCP"
                )
            elif entry.get("u") or entry.get("designorar"):
                item_norm["u"] = 1

            if entry.get("r") or entry.get("restaurar"):
                item_norm["r"] = 1
            else:
                raw_val = entry["v"] if "v" in entry else entry.get("valor_corrigido")
                val = parse_brl_number(raw_val)
                if val is not None:
                    item_norm["v"] = val
                    item_norm["j"] = str(
                        entry.get("j")
                        or entry.get("justificativa")
                        or entry.get("justificativa_retificacao")
                        or ""
                    ).strip()[:240]

            if item_norm:
                normalized[ctrl] = item_norm
        if not normalized:
            return 0
        return int(db.sync_pncp_retificacoes_from_request(normalized) or 0)

    def auto_atualizador(self) -> AutoAtualizador:
        return get_auto_atualizador()

    def _sent_admin_token(self) -> str:
        headers = getattr(self, "headers", None)
        if headers is None:
            return ""
        direct = str(headers.get("X-Radar-Token") or "").strip()
        if direct:
            return direct
        auth = str(headers.get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""

    def _is_direct_local_request(self) -> bool:
        """Verdadeiro só para acesso local direto, sem proxy e com Host local (evita DNS rebinding)."""
        client = getattr(self, "client_address", None) or ("",)
        if str(client[0]) not in _LOOPBACK_ADDRESSES:
            return False
        headers = getattr(self, "headers", None)
        if headers is None:
            return True
        if any(headers.get(h) for h in _PROXY_HEADERS):
            return False
        host = str(headers.get("Host") or "").strip()
        if host:
            try:
                hostname = (urllib.parse.urlsplit(f"//{host}").hostname or "").lower()
            except ValueError:
                return False
            if hostname not in _LOOPBACK_HOSTNAMES:
                return False
        return True

    def _is_write_authorized(self) -> bool:
        """Decide se a requisição pode gravar dados (POST ou reidratação por headers/cookies)."""
        expected = os.environ.get(ADMIN_TOKEN_ENV, "").strip()
        if expected:
            sent = self._sent_admin_token()
            return bool(sent) and hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8"))
        return self._is_direct_local_request()

    def _reject_unauthorized_write(self) -> None:
        if os.environ.get(ADMIN_TOKEN_ENV, "").strip():
            self.send_json_response(
                {"erro": "Token de administração ausente ou inválido. Envie o header X-Radar-Token."},
                status=401,
            )
        else:
            self.send_json_response(
                {
                    "erro": (
                        "Gravação desabilitada: defina RADAR_ADMIN_TOKEN no servidor para permitir "
                        "alterações fora do acesso local direto."
                    )
                },
                status=403,
            )

    def _resolve_route(self) -> Tuple[str, Dict[str, List[str]]]:
        """Separa o caminho da requisição e os parâmetros da query string."""
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        return parsed.path or "/", query

    def do_OPTIONS(self) -> None:
        # Sem Access-Control-Allow-Origin: o navegador bloqueia POST JSON vindo de outros sites.
        self.send_response(204)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        path, query = self._resolve_route()
        db = self.get_db()
        # Retificações e arquivamentos enviados por headers/cookies só valem para quem pode gravar.
        can_write = self._is_write_authorized()
        if can_write and path in (
            "/api/pautas",
            "/api/pautas/arquivadas",
            "/api/stats",
            "/api/telemetria",
        ):
            self._apply_request_pautas_arquivadas(db)
        if can_write and path in (
            "/api/stats",
            "/api/pncp/contratos",
            "/api/pncp/resumo",
            "/api/pncp/estatisticas",
            "/api/pncp/export/csv",
            "/api/pncp/retificacoes",
            "/api/pncp/ignorados",
            "/api/transparencia/resumo",
            "/api/transparencia/cruzamento",
            "/api/transparencia/dispensas",
            "/api/transparencia/export/csv",
        ):
            self._apply_request_pncp_retificacoes(db)

        # 1. Página principal / Dashboard HTML
        if path in ("/", "/index.html", "/dashboard"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            html_bytes = get_dashboard_html().encode("utf-8")
            self.send_header("Content-Length", str(len(html_bytes)))
            self.end_headers()
            self.wfile.write(html_bytes)
            return

        # 1.1 Arquivos estáticos
        if path.startswith("/static/"):
            rel_name = path[len("/static/"):].lstrip("/")
            static_file = resolve_static_file(rel_name)
            if static_file is not None:
                mime, _ = mimetypes.guess_type(str(static_file))
                data = static_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404, "Arquivo estático não encontrado")
            return

        # 1.2 Visualizar Clipping
        if path in ("/clipping", "/clipping.html"):
            clipping_file = OUTPUT_DIR / "clipping_do_dia.md"
            if clipping_file.exists():
                text = clipping_file.read_text(encoding="utf-8")
                html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <title>Clipping do Dia - Radar de Pautas de Imperatriz</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-800 p-8 max-w-4xl mx-auto font-sans">
  <div class="mb-4 flex items-center justify-between">
    <a href="/" class="text-sm text-blue-600 hover:underline">← Voltar ao Radar</a>
    <a href="/api/clipping" class="text-xs bg-slate-200 px-3 py-1.5 rounded font-medium">Ver Markdown Bruto</a>
  </div>
  <pre class="whitespace-pre-wrap font-sans bg-white p-8 rounded-xl shadow border border-slate-200 leading-relaxed text-sm">{text}</pre>
</body>
</html>"""
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                b = html.encode("utf-8")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            self.send_error(404, "Clipping não encontrado")
            return

        # 2. Endpoint REST: Listar pautas (suporta ?sort=recentes|score, ?dias=N, ?arquivadas=hide|only|all)
        if path == "/api/pautas":
            sort_mode = query.get("sort", ["recentes"])[0]
            dias_str = query.get("dias", ["0"])[0]
            arq_mode = query.get("arquivadas", ["hide"])[0].strip().lower()
            pautas = db.get_all_pautas(
                order_by=sort_mode,
                incluir_arquivadas=(arq_mode == "all"),
                somente_arquivadas=(arq_mode == "only"),
            )
            try:
                dias = int(dias_str)
            except ValueError:
                dias = 0
            if dias > 0:
                today = datetime.now().date()
                filtradas = []
                for p in pautas:
                    dt_str = (p.get("data_publicacao") or p.get("data_hora_coleta") or "")[:10]
                    try:
                        d = datetime.strptime(dt_str, "%Y-%m-%d").date()
                        if (today - d).days <= dias:
                            filtradas.append(p)
                    except ValueError:
                        pass
                pautas = filtradas
            self.send_json_response(pautas)
            return

        if path == "/api/pautas/arquivadas":
            self.send_json_response(db.get_all_pautas_arquivadas())
            return

        # 3. Endpoint REST: Estatísticas consolidadas
        if path == "/api/stats":
            pautas = db.get_all_pautas(incluir_arquivadas=False)
            pautas_arquivadas = db.get_all_pautas_arquivadas()
            execucoes = db.get_all_execucoes()
            fontes = db.get_all_fontes()
            pendencias = db.get_all_pendencias()
            pncp_resumo = db.get_pncp_resumo()
            today = datetime.now().date()
            pautas_48h = 0
            pautas_7d = 0
            for p in pautas:
                dt_str = (p.get("data_publicacao") or p.get("data_hora_coleta") or "")[:10]
                try:
                    age = (today - datetime.strptime(dt_str, "%Y-%m-%d").date()).days
                    if age <= 2:
                        pautas_48h += 1
                    if age <= 7:
                        pautas_7d += 1
                except ValueError:
                    pass
            stats = {
                "total_pautas": len(pautas),
                "pautas_arquivadas": len(pautas_arquivadas),
                "emergencias": sum(
                    1
                    for p in pautas
                    if p.get("prioridade") == "Emergência" or p.get("emergencia_sinalizada")
                ),
                "em_apuracao": sum(1 for p in pautas if p.get("nivel_verificacao") != "Confirmado"),
                "total_execucoes": len(execucoes),
                "ultima_execucao": execucoes[-1]["data_horario"] if execucoes else "",
                "total_fontes": len(fontes),
                "total_pendencias": len(pendencias),
                "pautas_48h": pautas_48h,
                "pautas_7d": pautas_7d,
                "total_contratos_pncp": pncp_resumo.get("total_contratos", 0),
                "valor_total_pncp": pncp_resumo.get("valor_total_global", 0.0),
                "por_ano_pncp": pncp_resumo.get("por_ano", {}),
                "storage_backend": getattr(db, "backend_name", "sqlite"),
            }
            self.send_json_response(stats)
            return

        # 3.1 Endpoints PNCP (Contratos 2024-2026)
        if path == "/api/pncp/contratos":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str.isdigit() else None
            orgao_val = query.get("orgao", [""])[0].strip() or None
            forn_val = query.get("fornecedor", [""])[0].strip() or None
            q_val = query.get("q", [""])[0].strip() or None
            cat_val = query.get("categoria", [""])[0].strip() or None
            uni_val = query.get("unidade", [""])[0].strip() or None
            ordem_val = query.get("ordem", ["recentes"])[0].strip() or "recentes"
            ign_mode = query.get("ignorados", ["hide"])[0].strip().lower()
            try:
                pagina_val = int(query.get("pagina", ["1"])[0])
            except ValueError:
                pagina_val = 1
            try:
                limite_val = int(query.get("limite", ["50"])[0])
            except ValueError:
                limite_val = 50
            result = db.query_pncp_contratos(
                ano=ano_val,
                orgao=orgao_val,
                fornecedor=forn_val,
                q=q_val,
                categoria=cat_val,
                unidade=uni_val,
                ordem=ordem_val,
                pagina=pagina_val,
                limite=limite_val,
                incluir_ignorados=(ign_mode == "all"),
                somente_ignorados=(ign_mode == "only"),
            )
            self.send_json_response(result)
            return

        if path == "/api/pncp/resumo":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str.isdigit() else None
            orgao_val = query.get("orgao", [""])[0].strip() or None
            resumo = db.get_pncp_resumo(ano=ano_val, orgao=orgao_val)
            self.send_json_response(resumo)
            return

        if path == "/api/pncp/estatisticas":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str.isdigit() else None
            orgao_val = query.get("orgao", [""])[0].strip() or None
            estatisticas = db.get_pncp_estatisticas(ano=ano_val, orgao=orgao_val)
            self.send_json_response(estatisticas)
            return

        if path == "/api/pncp/export/csv":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str.isdigit() else None
            orgao_val = query.get("orgao", [""])[0].strip() or None
            forn_val = query.get("fornecedor", [""])[0].strip() or None
            q_val = query.get("q", [""])[0].strip() or None
            cat_val = query.get("categoria", [""])[0].strip() or None
            uni_val = query.get("unidade", [""])[0].strip() or None
            ordem_val = query.get("ordem", ["recentes"])[0].strip() or "recentes"
            ign_mode = query.get("ignorados", ["hide"])[0].strip().lower()

            all_rows: List[Dict[str, Any]] = []
            pg = 1
            while True:
                page_res = db.query_pncp_contratos(
                    ano=ano_val,
                    orgao=orgao_val,
                    fornecedor=forn_val,
                    q=q_val,
                    categoria=cat_val,
                    unidade=uni_val,
                    ordem=ordem_val,
                    pagina=pg,
                    limite=200,
                    incluir_ignorados=(ign_mode == "all"),
                    somente_ignorados=(ign_mode == "only"),
                )
                all_rows.extend(page_res["items"])
                if pg >= page_res["total_paginas"]:
                    break
                pg += 1

            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=";")
            cols = [
                "numero_controle_pncp",
                "ano_contrato",
                "orgao_sigla",
                "orgao_nome",
                "unidade_nome",
                "categoria_tematica",
                "numero_contrato",
                "processo",
                "tipo_contrato",
                "fornecedor_cnpj",
                "fornecedor_nome",
                "objeto",
                "valor_inicial",
                "valor_global",
                "data_assinatura",
                "data_publicacao",
                "data_vigencia_inicio",
                "data_vigencia_fim",
                "retificado",
                "valor_original_pncp",
                "justificativa_retificacao",
                "url_pncp",
            ]
            writer.writerow(cols)
            for row in all_rows:
                writer.writerow([row.get(c, "") for c in cols])

            data = buf.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="pncp_contratos_imperatriz.csv"')
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if path == "/api/pncp/retificacoes":
            self.send_json_response(db.get_all_pncp_retificacoes())
            return

        if path == "/api/pncp/ignorados":
            self.send_json_response(db.get_all_pncp_ignorados())
            return

        # 3.2 Endpoints Transparência Fiorilli × PNCP (2025–2026)
        if path == "/api/transparencia/resumo":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str in ("2025", "2026") else None
            # Carrega 2025+2026 (e anos anteriores, para contratos ainda vigentes) lado a lado
            pncp_rows = db.get_effective_pncp_contratos(ano=None, anos=PNCP_ANOS_CRUZAMENTO)
            receitas_rows = [
                r for r in db.get_fiorilli_receitas(ano=None) if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            orgaos_rows = [
                r for r in db.get_fiorilli_despesas_orgaos(ano=None) if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            forn_rows = [
                r
                for r in db.get_fiorilli_despesas_fornecedores(ano=None)
                if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            con_rows = [
                r for r in db.get_fiorilli_contratos(ano=None) if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            meta = db.get_fiorilli_meta()
            resumo = build_transparencia_resumo(
                pncp_rows=pncp_rows,
                receitas_rows=receitas_rows,
                desp_orgaos_rows=orgaos_rows,
                desp_forn_rows=forn_rows,
                fiorilli_contratos_rows=con_rows,
                ano=ano_val,
                meta=meta,
                pncp_cobertura=pncp_cobertura_registrada(db),
            )
            self.send_json_response(resumo)
            return

        if path == "/api/transparencia/cruzamento":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str in ("2025", "2026") else None
            alerta_val = query.get("alerta", ["TODOS"])[0].strip() or "TODOS"
            q_val = query.get("q", [""])[0].strip() or None
            ordem_val = query.get("ordem", ["pago_desc"])[0].strip() or "pago_desc"
            try:
                pagina_val = int(query.get("pagina", ["1"])[0])
            except ValueError:
                pagina_val = 1
            try:
                limite_val = int(query.get("limite", ["50"])[0])
            except ValueError:
                limite_val = 50
            try:
                corte_val = float(query.get("corte", ["100000"])[0])
            except ValueError:
                corte_val = 100000.0

            # Todos os anos: o filtro por exercício é feito no cruzamento, que precisa dos anteriores
            pncp_rows = db.get_effective_pncp_contratos(ano=None, anos=PNCP_ANOS_CRUZAMENTO)
            forn_rows = [
                r
                for r in db.get_fiorilli_despesas_fornecedores(ano=ano_val)
                if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            con_rows = [
                r for r in db.get_fiorilli_contratos(ano=ano_val) if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            res = build_cruzamento_fornecedores(
                pncp_rows=pncp_rows,
                desp_forn_rows=forn_rows,
                fiorilli_contratos_rows=con_rows,
                ano=ano_val,
                alerta=alerta_val,
                q=q_val,
                ordem=ordem_val,
                pagina=pagina_val,
                limite=limite_val,
                corte_sem_pncp=corte_val,
                pncp_cobertura=pncp_cobertura_registrada(db),
            )
            self.send_json_response(res)
            return

        if path == "/api/transparencia/dispensas":
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str in ("2025", "2026") else None
            tipo_val = query.get("tipo", [""])[0].strip() or None
            status_val = query.get("status_pncp", [""])[0].strip() or None
            cat_val = query.get("categoria", [""])[0].strip() or None
            ent_val = query.get("entidade", [""])[0].strip() or None
            q_val = query.get("q", [""])[0].strip() or None
            inc_rem = query.get("remanescentes", ["0"])[0].strip().lower() in ("1", "true", "sim")
            try:
                pagina_val = int(query.get("pagina", ["1"])[0])
            except ValueError:
                pagina_val = 1
            try:
                limite_val = int(query.get("limite", ["50"])[0])
            except ValueError:
                limite_val = 50

            pncp_rows = db.get_effective_pncp_contratos(ano=ano_val, anos=FIORILLI_DEFAULT_YEARS)
            con_rows = [
                r
                for r in db.get_fiorilli_contratos(ano=ano_val, somente_diretas=True)
                if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
            ]
            res = build_auditoria_dispensas(
                pncp_rows=pncp_rows,
                fiorilli_contratos_rows=con_rows,
                ano=ano_val,
                tipo=tipo_val,
                status_pncp=status_val,
                categoria=cat_val,
                entidade=ent_val,
                q=q_val,
                pagina=pagina_val,
                limite=limite_val,
                incluir_remanescentes=inc_rem,
                pncp_cobertura=pncp_cobertura_registrada(db),
            )
            self.send_json_response(res)
            return

        if path == "/api/transparencia/export/csv":
            visao = query.get("visao", ["cruzamento"])[0].strip().lower()
            ano_str = query.get("ano", [""])[0].strip()
            ano_val = int(ano_str) if ano_str in ("2025", "2026") else None
            q_val = query.get("q", [""])[0].strip() or None
            pncp_rows = db.get_effective_pncp_contratos(ano=ano_val, anos=FIORILLI_DEFAULT_YEARS)
            cobertura_csv = pncp_cobertura_registrada(db)

            buf = io.StringIO()
            writer = csv.writer(buf, delimiter=";")

            if visao == "dispensas":
                tipo_val = query.get("tipo", [""])[0].strip() or None
                status_val = query.get("status_pncp", [""])[0].strip() or None
                cat_val = query.get("categoria", [""])[0].strip() or None
                ent_val = query.get("entidade", [""])[0].strip() or None
                inc_rem = query.get("remanescentes", ["0"])[0].strip().lower() in ("1", "true", "sim")
                con_rows = [
                    r
                    for r in db.get_fiorilli_contratos(ano=ano_val, somente_diretas=True)
                    if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
                ]
                all_disp: List[Dict[str, Any]] = []
                pg = 1
                while True:
                    page_res = build_auditoria_dispensas(
                        pncp_rows=pncp_rows,
                        fiorilli_contratos_rows=con_rows,
                        ano=ano_val,
                        tipo=tipo_val,
                        status_pncp=status_val,
                        categoria=cat_val,
                        entidade=ent_val,
                        q=q_val,
                        pagina=pg,
                        limite=200,
                        incluir_remanescentes=inc_rem,
                        pncp_cobertura=cobertura_csv,
                    )
                    all_disp.extend(page_res["items"])
                    if pg >= page_res["total_paginas"]:
                        break
                    pg += 1
                cols = [
                    "ano",
                    "entidade",
                    "secretaria_gestora",
                    "tipo_contratacao",
                    "fund_legal",
                    "modalidade",
                    "codigo",
                    "processo_adm",
                    "fornecedor_doc",
                    "fornecedor_nome",
                    "categoria_tematica",
                    "objeto",
                    "valor_contrato",
                    "valor_aditado",
                    "valor_efetivo",
                    "empenhado",
                    "liquidado",
                    "status_pncp",
                    "pncp_match_controle",
                    "pncp_match_valor",
                    "motivo_pncp",
                ]
                writer.writerow(cols)
                for row in all_disp:
                    writer.writerow([row.get(c, "") for c in cols])
                filename = "dispensas_transparencia_imperatriz.csv"
            else:
                alerta_val = query.get("alerta", ["TODOS"])[0].strip() or "TODOS"
                ordem_val = query.get("ordem", ["pago_desc"])[0].strip() or "pago_desc"
                pncp_rows = db.get_effective_pncp_contratos(ano=None, anos=PNCP_ANOS_CRUZAMENTO)
                forn_rows = [
                    r
                    for r in db.get_fiorilli_despesas_fornecedores(ano=ano_val)
                    if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
                ]
                con_rows = [
                    r for r in db.get_fiorilli_contratos(ano=ano_val) if int(r.get("ano") or 0) in FIORILLI_DEFAULT_YEARS
                ]
                all_cross: List[Dict[str, Any]] = []
                pg = 1
                while True:
                    page_res = build_cruzamento_fornecedores(
                        pncp_rows=pncp_rows,
                        desp_forn_rows=forn_rows,
                        fiorilli_contratos_rows=con_rows,
                        ano=ano_val,
                        alerta=alerta_val,
                        q=q_val,
                        ordem=ordem_val,
                        pagina=pg,
                        limite=200,
                        pncp_cobertura=cobertura_csv,
                    )
                    all_cross.extend(page_res["items"])
                    if pg >= page_res["total_paginas"]:
                        break
                    pg += 1
                cols = [
                    "fornecedor_doc",
                    "tipo_pessoa",
                    "fornecedor_nome",
                    "cidade",
                    "alerta_codigo",
                    "alerta_label",
                    "alerta_conclusivo",
                    "alerta_observacao",
                    "pncp_contratos_qtd",
                    "pncp_valor",
                    "pncp_anteriores_qtd",
                    "pncp_anteriores_valor",
                    "fiorilli_contratos_qtd",
                    "fiorilli_valor",
                    "fiorilli_dispensas_qtd",
                    "fiorilli_dispensas_valor",
                    "empenhado",
                    "liquidado",
                    "pago",
                    "diferenca_pago_pncp",
                    "pct_pago_sobre_pncp",
                ]
                writer.writerow(cols)
                for row in all_cross:
                    writer.writerow([row.get(c, "") for c in cols])
                filename = "cruzamento_pncp_transparencia_imperatriz.csv"

            data = buf.getvalue().encode("utf-8-sig")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # 4. Endpoint REST: Clipping diário em Markdown
        if path == "/api/auto-atualizar":
            st = self.auto_atualizador().status()
            st["habilitada"] = auto_atualizacao_habilitada()
            self.send_json_response(st)
            return

        if path == "/api/clipping":
            clipping_file = OUTPUT_DIR / "clipping_do_dia.md"
            if clipping_file.exists():
                raw = clipping_file.read_text(encoding="utf-8").encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            else:
                self.send_error(404, "Clipping não encontrado")
            return

        # 5. Endpoint Download: Exportar CSV
        if path in ("/api/export/csv", "/export/pautas.csv"):
            csv_file = OUTPUT_DIR / "pautas_novas.csv"
            if not csv_file.exists():
                pautas = db.get_all_pautas()
                from src.exporter import CSVExporter

                CSVExporter(db).export_pautas_novas(pautas)

            data = csv_file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="pautas_imperatriz.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # 6. Endpoint REST: Fontes cadastradas
        if path == "/api/fontes":
            fontes = db.get_all_fontes()
            self.send_json_response(fontes)
            return

        # 7. Endpoint REST: Dossiês / Pendências de apuração
        if path == "/api/pendencias":
            pendencias = db.get_pendencias_detalhadas()
            self.send_json_response(pendencias)
            return

        # 8. Endpoint REST: Telemetria honesta do Rio Tocantins (Estação ANA 23800000)
        if path == "/api/telemetria":
            ana_token = os.environ.get("ANA_TELEMETRIA_TOKEN", "").strip()
            pautas = db.get_all_pautas(order_by="recentes", incluir_arquivadas=False)
            alertas_rio = []
            for p in pautas:
                txt = f"{p.get('titulo_jornalistico', '')} {p.get('resumo_3_linhas', '')} {p.get('bairros_locais_instituicoes', '')}".lower()
                if any(k in txt for k in ("rio tocantins", "defesa civil", "alagamento", "inmet", "chuva", "enchente")):
                    alertas_rio.append(
                        {
                            "id_pauta": p.get("id_pauta", ""),
                            "titulo_jornalistico": p.get("titulo_jornalistico", ""),
                            "data_publicacao": p.get("data_publicacao", ""),
                            "url_original": p.get("url_original", ""),
                        }
                    )
                if len(alertas_rio) >= 3:
                    break

            self.send_json_response(
                {
                    "estacao_codigo": ANA_ESTACAO_IMPERATRIZ_CODIGO,
                    "estacao_nome": "Estação Fluviométrica Imperatriz (Rio Tocantins)",
                    "configurada_api_ana": bool(ana_token),
                    "status": (
                        "Credencial ANA detectada — consulte portal oficial para validação de série telemétrica."
                        if ana_token
                        else "Aguardando credencial ANA (ANA_TELEMETRIA_TOKEN)"
                    ),
                    "nivel_atual": None,
                    "nota_transparencia": (
                        "A API automática do HidroWeb/ANA exige cadastro prévio via telemetria@ana.gov.br. "
                        "Nenhum nível fictício é exibido sem leitura oficial autenticada."
                    ),
                    "url_oficial": "https://www.snirh.gov.br/hidrotelemetria/gerarGrafico.aspx",
                    "url_defesa_civil": "https://imperatriz.ma.gov.br/defesa-civil",
                    "alertas_locais": alertas_rio,
                }
            )
            return

        # Rota não encontrada
        self.send_error(404, "Endpoint não encontrado")

    def _read_json_body(self) -> Dict[str, Any]:
        """Lê com segurança payload JSON opcional do corpo da requisição POST."""
        rfile = getattr(self, "rfile", None)
        if not rfile:
            return {}
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        try:
            raw = rfile.read(min(length, 262_144)).decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def do_POST(self) -> None:
        path, _ = self._resolve_route()
        content_type = str(self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            # Exigir JSON força o preflight CORS, que este servidor não autoriza para outras origens.
            self.send_json_response(
                {"erro": "Envie o corpo como JSON (Content-Type: application/json)."},
                status=415,
            )
            return
        if not self._is_write_authorized():
            self._reject_unauthorized_write()
            return
        if path == "/api/pncp/sync":
            # Tratado antes de get_db(): qualquer falha (inclusive ao abrir o banco) vira JSON
            self._handle_pncp_sync()
            return
        if path == "/api/auto-atualizar":
            body = self._read_json_body()
            if not auto_atualizacao_habilitada():
                self.send_json_response({"desabilitada": True, "em_andamento": False, "iniciadas": [], "tarefas": {}})
                return
            consultas = body.get("consultas_adicionais")
            contexto = {"consultas_adicionais": [str(c) for c in consultas][:30] if isinstance(consultas, list) else None}
            self.send_json_response(self.auto_atualizador().disparar(contexto, forcar=bool(body.get("forcar"))))
            return
        db = self.get_db()
        body = self._read_json_body()

        # 1. Disparar ciclo de pautas sob demanda
        if path == "/api/executar":
            try:
                consultas = body.get("consultas_adicionais")
                if not isinstance(consultas, list):
                    consultas = None
                res = self.auto_atualizador().executar_manual(
                    "varredura", lambda: run_radar_cycle(modo="web", consultas_adicionais=consultas)
                )
                self.send_json_response(res)
            except TarefaOcupada as e:
                self.send_json_response({"erro": str(e), "em_andamento": True}, status=409)
            except Exception as e:
                logger.error("Erro ao executar ciclo via web: %s", e, exc_info=True)
                self.send_json_response({"erro": str(e)}, status=500)
            return

        # 2.0b Sincronizar Portal Transparência Fiorilli (2025-2026) sob demanda
        if path == "/api/transparencia/sync":
            try:
                raw_anos = body.get("anos")
                if isinstance(raw_anos, list):
                    anos = [int(a) for a in raw_anos if str(a) in ("2025", "2026")]
                else:
                    anos = []
                if not anos:
                    anos = list(FIORILLI_DEFAULT_YEARS)
                res = self.auto_atualizador().executar_manual(
                    "fiorilli", lambda: sincronizar_fiorilli(db, anos)
                )
                self.send_json_response(res, status=502 if res.get("erro") else 200)
            except TarefaOcupada as e:
                self.send_json_response({"erro": str(e), "em_andamento": True}, status=409)
            except Exception as e:
                logger.error("Erro ao sincronizar Fiorilli via web: %s", e, exc_info=True)
                self.send_json_response({"erro": str(e)}, status=500)
            return

        # 2.1 Retificar ou restaurar valor de um contrato do PNCP
        if path == "/api/pncp/retificar":
            ctrl = str(body.get("numero_controle_pncp") or "").strip()
            if not ctrl:
                self.send_json_response(
                    {"erro": "Informe numero_controle_pncp do contrato."},
                    status=400,
                )
                return

            if bool(body.get("restaurar")):
                self._apply_request_pncp_retificacoes(db)
                restored = db.restaurar_pncp_contrato(ctrl)
                if not restored:
                    self.send_json_response(
                        {"erro": "Nenhuma retificação localizada para este contrato."},
                        status=404,
                    )
                    return
                self.send_json_response({"ok": True, "restaurado": True, "contrato": restored})
                return

            valor = parse_brl_number(body.get("valor_corrigido"))
            if valor is None:
                self.send_json_response(
                    {"erro": "Informe um valor monetário válido maior que zero (ex.: 1.682.632,75)."},
                    status=400,
                )
                return

            justificativa = str(body.get("justificativa") or "").strip()
            updated = db.retificar_pncp_contrato(ctrl, valor, justificativa)
            if not updated:
                self.send_json_response(
                    {"erro": "Contrato PNCP não encontrado no banco local."},
                    status=404,
                )
                return
            self.send_json_response({"ok": True, "contrato": updated})
            return

        # 2.2 Reaplicar retificações e ignorados salvos em lote (reidratação serverless SQLite)
        if path == "/api/pncp/retificacoes/sync":
            backend_name = getattr(db, "backend_name", "sqlite")
            if backend_name != "sqlite":
                self.send_json_response(
                    {"ok": True, "aplicadas": 0, "storage_backend": backend_name}
                )
                return
            lista = body.get("retificacoes")
            ign_lista = body.get("ignorados")
            if lista is None and isinstance(ign_lista, list):
                lista = []
            if not isinstance(lista, list):
                self.send_json_response(
                    {"erro": "Campo 'retificacoes' deve ser uma lista."},
                    status=400,
                )
                return
            normalizadas: List[Dict[str, Any]] = []
            for item in lista[:200]:
                if not isinstance(item, dict):
                    continue
                ctrl = str(item.get("numero_controle_pncp") or "").strip()
                val = parse_brl_number(item.get("valor_corrigido"))
                if ctrl and val is not None:
                    normalizadas.append(
                        {
                            "numero_controle_pncp": ctrl,
                            "valor_corrigido": val,
                            "justificativa": str(
                                item.get("justificativa") or item.get("justificativa_retificacao") or ""
                            ).strip(),
                        }
                    )
            aplicadas = db.apply_pncp_retificacoes_batch(normalizadas)
            if isinstance(ign_lista, list):
                for item in ign_lista[:200]:
                    if not isinstance(item, dict):
                        continue
                    ctrl = str(item.get("numero_controle_pncp") or "").strip()
                    motivo = str(item.get("motivo") or "Contrato duplicado no PNCP").strip()
                    if ctrl and db.ignorar_pncp_contrato(ctrl, motivo):
                        aplicadas += 1
            self.send_json_response(
                {"ok": True, "aplicadas": aplicadas, "storage_backend": backend_name}
            )
            return

        # 2.3 Ignorar ou restaurar contrato duplicado do PNCP
        if path == "/api/pncp/ignorar":
            ctrl = str(body.get("numero_controle_pncp") or "").strip()
            if not ctrl:
                self.send_json_response(
                    {"erro": "Informe numero_controle_pncp do contrato."},
                    status=400,
                )
                return

            if bool(body.get("restaurar")):
                self._apply_request_pncp_retificacoes(db)
                restored = db.restaurar_pncp_ignorado(ctrl)
                if not restored:
                    self.send_json_response(
                        {"erro": "Contrato PNCP não encontrado."},
                        status=404,
                    )
                    return
                self.send_json_response({"ok": True, "restaurado": True, "contrato": restored})
                return

            motivo = str(body.get("motivo") or "Contrato duplicado no PNCP").strip()
            updated = db.ignorar_pncp_contrato(ctrl, motivo)
            if not updated:
                self.send_json_response(
                    {"erro": "Contrato PNCP não encontrado no banco."},
                    status=404,
                )
                return
            self.send_json_response({"ok": True, "ignorado": True, "contrato": updated})
            return

        # 2.4 Arquivar ou restaurar pauta no feed e na capa do dia
        if path == "/api/pautas/arquivar":
            id_pauta = str(body.get("id_pauta") or "").strip()
            if not id_pauta:
                self.send_json_response(
                    {"erro": "Informe id_pauta da pauta."},
                    status=400,
                )
                return

            if bool(body.get("restaurar")):
                self._apply_request_pautas_arquivadas(db)
                restored = db.restaurar_pauta_arquivada(id_pauta)
                if not restored:
                    self.send_json_response(
                        {"erro": "Pauta não encontrada."},
                        status=404,
                    )
                    return
                self.send_json_response({"ok": True, "restaurado": True, "pauta": restored})
                return

            motivo = str(body.get("motivo") or "Arquivada pela redação").strip()
            updated = db.arquivar_pauta(id_pauta, motivo)
            if not updated:
                self.send_json_response(
                    {"erro": "Pauta não encontrada no banco."},
                    status=404,
                )
                return
            self.send_json_response({"ok": True, "arquivada": True, "pauta": updated})
            return

        # 3. Cadastrar nova fonte pela UI sem modificar código
        if path == "/api/fontes":
            nome = str(body.get("nome") or "").strip()
            tipo = str(body.get("tipo") or "Portal Web").strip()
            url = str(body.get("url") or "").strip()
            prioridade = str(body.get("prioridade") or "Média").strip()
            temas = str(body.get("temas") or "Imperatriz;notícias").strip()

            if not nome or not SourceManager.validate_url(url):
                self.send_json_response(
                    {"erro": "Informe nome válido e URL completa com http:// ou https://."},
                    status=400,
                )
                return

            sm = SourceManager(db, FONTES_CSV_PATH)
            ok = sm.register_new_source(
                nome=nome,
                tipo=tipo,
                url=url,
                prioridade=prioridade,
                temas=temas,
                persist_csv=True,
            )
            if not ok:
                self.send_json_response(
                    {"erro": "Não foi possível cadastrar a fonte informada."},
                    status=400,
                )
                return
            self.send_json_response(
                {
                    "ok": True,
                    "fonte": {
                        "nome": nome,
                        "tipo": tipo,
                        "url": url,
                        "prioridade": prioridade,
                        "temas": temas,
                    },
                },
                status=201,
            )
            return

        self.send_error(404, "Endpoint POST não encontrado")

    def _handle_pncp_sync(self) -> None:
        """Sincroniza um lote do PNCP. Sempre responde JSON, com segredos mascarados."""
        if self.auto_atualizador().tarefa_rodando("pncp"):
            self.send_json_response(
                {"ok": False, "erro": "A atualização automática do PNCP está em andamento. Aguarde terminar.", "em_andamento": True},
                status=409,
            )
            return
        try:
            body = self._read_json_body()
            db = self.get_db()
            self._apply_request_pncp_retificacoes(db)
            cursor = body.get("proxima_janela")
            modo = str(body.get("modo") or "auto").strip().lower()
            estado_cliente = body.get("estado_cliente")
            res = executar_lote(
                db,
                PncpClient.normalize_contrato,
                cursor=cursor if cursor else None,
                modo=modo,
                estado_cliente=estado_cliente if isinstance(estado_cliente, dict) else None,
            )
            res["storage_backend"] = getattr(db, "backend_name", "sqlite")
            res["lock_escopo"] = getattr(db, "sync_lock_escopo", "banco")
            res["estado_persistente"] = bool(getattr(db, "sync_estado_persistente", True))
            if res["storage_backend"] == "sqlite" and IS_RENDER:
                res["aviso_armazenamento"] = (
                    "Banco SQLite no disco temporário do Render: os dados se perdem quando o serviço reinicia. "
                    "Configure o Supabase para guardar de vez."
                )
            if res["erros"] and res["inseridos"] == 0 and res["atualizados"] == 0 and res["janelas_concluidas"] == 0:
                res["erro"] = "O PNCP não respondeu corretamente. Nada foi perdido; tente continuar mais tarde."
                self.send_json_response(res, status=502)
                return
            self.send_json_response(res)
        except SyncEmAndamento as exc:
            self.send_json_response({"ok": False, "erro": str(exc), "em_andamento": True}, status=409)
        except CursorInvalido as exc:
            self.send_json_response({"ok": False, "erro": f"Cursor de sincronização inválido: {exc}"}, status=400)
        except Exception as exc:
            logger.error("Erro ao sincronizar PNCP via web: %s", mask_secrets(str(exc)))
            self.send_json_response(
                {
                    "ok": False,
                    "erro": "Falha interna ao sincronizar o PNCP.",
                    "parcial": True,
                },
                status=500,
            )

    def send_json_response(self, data: Any, status: int = 200) -> None:
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(json_bytes)))
        self.end_headers()
        self.wfile.write(json_bytes)


# ---------------------------------------------------------------------------
# Atualização automática ao abrir o painel (POST /api/auto-atualizar)
# ---------------------------------------------------------------------------
FIORILLI_INDISPONIVEL = (
    "Portal Transparência Fiorilli (porta :8079) indisponível ou lento neste ambiente. "
    "Você pode sincronizar localmente com `python scripts/fiorilli_sync.py --anos 2025 2026` "
    "e subir ao Supabase com `python scripts/supabase_sync.py --only-fiorilli`."
)
# Lotes do PNCP por rodada automática (~40 s cada); o restante continua na próxima abertura.
PNCP_AUTO_MAX_LOTES = 15
# Se outra sessão mexeu numa carga pendente há menos que isto, a automática não interfere.
PNCP_OUTRA_SESSAO_SEGUNDOS = 180


def sincronizar_fiorilli(db: Any, anos: List[int]) -> Dict[str, Any]:
    res = FiorilliClient(db, timeout=25).sync_imperatriz(anos=anos)
    res["storage_backend"] = getattr(db, "backend_name", "sqlite")
    if res.get("total_registros", 0) == 0 and res.get("erros"):
        res["erro"] = FIORILLI_INDISPONIVEL
    return res


def pncp_carga_ativa_em_outra_sessao(estado: Dict[str, Any], agora: Optional[datetime] = None) -> bool:
    """Carga pendente com lote recente = alguém (outra aba, script) está conduzindo; não atrapalhar."""
    if not estado or not estado.get("cursor_pendente"):
        return False
    try:
        ultima = datetime.fromisoformat(str(estado.get("ultima_execucao") or ""))
    except ValueError:
        return False
    agora = agora or datetime.now()
    return abs((agora - ultima).total_seconds()) < PNCP_OUTRA_SESSAO_SEGUNDOS


def auto_pncp(db: Any, lote: Callable[..., Dict[str, Any]] = executar_lote, max_lotes: int = PNCP_AUTO_MAX_LOTES) -> Dict[str, Any]:
    estado = db.get_pncp_sync_state() or {}
    if pncp_carga_ativa_em_outra_sessao(estado):
        return {"pulado": True, "motivo": "Carga do PNCP em andamento em outra sessão."}
    tot = {"inseridos": 0, "atualizados": 0, "ignorados": 0, "lotes": 0}
    ultimo: Dict[str, Any] = {}
    for _ in range(max_lotes):
        try:
            ultimo = lote(db, PncpClient.normalize_contrato, modo="auto")
        except SyncEmAndamento:
            return dict(tot, pulado=True, motivo="Outra sincronização do PNCP está em andamento.")
        tot["lotes"] += 1
        for k in ("inseridos", "atualizados", "ignorados"):
            tot[k] += int(ultimo.get(k) or 0)
        if ultimo.get("erros"):
            res = dict(tot, parcial=bool(ultimo.get("parcial")), progresso_pct=ultimo.get("progresso_pct"))
            if not (tot["inseridos"] or tot["atualizados"] or ultimo.get("janelas_concluidas")):
                res["erro"] = "O PNCP não respondeu corretamente. Nada foi perdido; tenta de novo na próxima abertura."
            return res
        if not ultimo.get("parcial"):
            break
    return dict(
        tot,
        parcial=bool(ultimo.get("parcial")),
        progresso_pct=ultimo.get("progresso_pct"),
        ultima_data_sincronizada=ultimo.get("ultima_data_sincronizada"),
    )


def _executores_padrao() -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    def varredura(ctx: Dict[str, Any]) -> Dict[str, Any]:
        return run_radar_cycle(modo="web", consultas_adicionais=ctx.get("consultas_adicionais"))

    def pncp(ctx: Dict[str, Any]) -> Dict[str, Any]:
        return auto_pncp(RadarAPIHandler.get_db())

    def fiorilli(ctx: Dict[str, Any]) -> Dict[str, Any]:
        return sincronizar_fiorilli(RadarAPIHandler.get_db(), list(FIORILLI_DEFAULT_YEARS))

    return {"varredura": varredura, "pncp": pncp, "fiorilli": fiorilli}


_AUTO: Optional[AutoAtualizador] = None


def get_auto_atualizador() -> AutoAtualizador:
    global _AUTO
    if _AUTO is None:
        _AUTO = AutoAtualizador(_executores_padrao())
    return _AUTO


def run_web_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), RadarAPIHandler)
    logger.info("Radar de Pautas de Imperatriz rodando em http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Encerrando servidor web...")
        server.server_close()


if __name__ == "__main__":
    port_env = int(os.environ.get("PORT", 8000))
    run_web_server(port=port_env)
