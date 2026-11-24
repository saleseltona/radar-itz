"""
Servidor Web e API RESTful nativa (sem dependências externas) para o Radar de Pautas de Imperatriz.
Disponibiliza um Dashboard Jornalístico interativo com busca, filtros por categoria,
visualização de evidências, disparo de novos ciclos de varredura e download de relatórios.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import mimetypes
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from main import run_radar_cycle
from src.config import BASE_DIR, DB_PATH, OUTPUT_DIR, ensure_runtime_files
from src.database import RadarDatabase
from src.source_manager import SourceManager

logger = logging.getLogger("radar_imperatriz.web")

STATIC_DIR = BASE_DIR / "static"


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
        const res = await fetch('/api/executar', { method: 'POST' });
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


class RadarAPIHandler(BaseHTTPRequestHandler):
    """Manipulador HTTP REST e servidor estático embutido (compatível com local e Vercel)."""

    _db: Optional[RadarDatabase] = None

    @classmethod
    def get_db(cls) -> RadarDatabase:
        """Inicializa o banco SQLite sob demanda (copiando para /tmp na Vercel se necessário)."""
        ensure_runtime_files()
        if cls._db is None or cls._db.db_path != DB_PATH or not DB_PATH.exists():
            cls._db = RadarDatabase(DB_PATH)
        return cls._db

    def _resolve_route(self) -> Tuple[str, Dict[str, List[str]]]:
        """Resolve a rota original considerando rewrites da Vercel (`?__route=...`)."""
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        route_param = query.get("__route", [None])[0]
        if route_param:
            path = route_param if route_param.startswith("/") else f"/{route_param}"
        else:
            path = parsed.path or "/"
        if path in ("/api/index", "/api/index.py", "/api"):
            path = "/"
        return path, query

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        path, query = self._resolve_route()
        db = self.get_db()

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
            static_file = STATIC_DIR / rel_name
            if static_file.is_file():
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

        # 2. Endpoint REST: Listar pautas
        if path == "/api/pautas":
            pautas = db.get_all_pautas()
            self.send_json_response(pautas)
            return

        # 3. Endpoint REST: Estatísticas
        if path == "/api/stats":
            pautas = db.get_all_pautas()
            execucoes = db.get_all_execucoes()
            fontes = db.get_all_fontes()
            stats = {
                "total_pautas": len(pautas),
                "emergencias": sum(
                    1
                    for p in pautas
                    if p.get("prioridade") == "Emergência" or p.get("emergencia_sinalizada")
                ),
                "em_apuracao": sum(1 for p in pautas if p.get("nivel_verificacao") != "Confirmado"),
                "total_execucoes": len(execucoes),
                "total_fontes": len(fontes),
            }
            self.send_json_response(stats)
            return

        # 4. Endpoint REST: Clipping diário em Markdown
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

        # Rota não encontrada
        self.send_error(404, "Endpoint não encontrado")

    def do_POST(self) -> None:
        path, _ = self._resolve_route()
        self.get_db()

        # Disparar ciclo sob demanda
        if path == "/api/executar":
            try:
                res = run_radar_cycle(modo="web")
                self.send_json_response(res)
            except Exception as e:
                logger.error("Erro ao executar ciclo via web: %s", e, exc_info=True)
                self.send_json_response({"erro": str(e)}, status=500)
            return

        self.send_error(404, "Endpoint POST não encontrado")

    def send_json_response(self, data: Any, status: int = 200) -> None:
        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(json_bytes)))
        self.end_headers()
        self.wfile.write(json_bytes)


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
