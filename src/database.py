"""
Módulo de Banco de Dados (SQLite) para persistência histórica de fontes, execuções,
páginas coletadas, pautas, evidências, atualizações, pendências e erros.
Não apaga registros antigos silenciosamente e preserva todo o histórico.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from src.config import DB_PATH, is_supabase_configured
from src.pncp_analytics import (
    build_pncp_estatisticas,
    classify_pncp_contrato,
    is_fornecedor_cpf,
)


class RadarDatabase:
    """Gerencia a persistência relacional em SQLite do Radar de Pautas de Imperatriz."""

    backend_name: str = "sqlite"

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fontes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    prioridade TEXT NOT NULL,
                    temas TEXT NOT NULL,
                    situacao_funcionamento TEXT DEFAULT 'Cadastrada',
                    ultima_consulta TEXT DEFAULT '',
                    ultima_coleta_sucesso TEXT DEFAULT '',
                    metodo_acesso TEXT DEFAULT 'HTTP Direto / Verificador',
                    observacoes_limitacoes TEXT DEFAULT '',
                    erro_identificado TEXT DEFAULT '',
                    proxima_acao_recomendada TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS execucoes (
                    id_execucao TEXT PRIMARY KEY,
                    data_horario TEXT NOT NULL,
                    modo TEXT NOT NULL DEFAULT 'manual',
                    fontes_consultadas INTEGER DEFAULT 0,
                    fontes_com_falha INTEGER DEFAULT 0,
                    paginas_lidas INTEGER DEFAULT 0,
                    pautas_novas INTEGER DEFAULT 0,
                    pautas_atualizadas INTEGER DEFAULT 0,
                    duplicidades_eliminadas INTEGER DEFAULT 0,
                    itens_descartados INTEGER DEFAULT 0,
                    duracao_segundos REAL DEFAULT 0.0,
                    situacao_execucao TEXT NOT NULL DEFAULT 'EM_ANDAMENTO',
                    resumo_json TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS paginas_coletadas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_execucao TEXT NOT NULL,
                    url TEXT NOT NULL,
                    fonte_nome TEXT NOT NULL,
                    titulo TEXT NOT NULL,
                    data_publicacao TEXT DEFAULT '',
                    data_acontecimento TEXT DEFAULT '',
                    data_coleta TEXT NOT NULL,
                    metodo_coleta TEXT NOT NULL,
                    status_leitura TEXT NOT NULL,
                    conteudo_hash TEXT NOT NULL,
                    conteudo_extraido TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pautas (
                    id_pauta TEXT PRIMARY KEY,
                    prioridade TEXT NOT NULL,
                    pontuacao_editorial INTEGER NOT NULL,
                    justificativa_pontuacao TEXT NOT NULL,
                    emergencia_sinalizada INTEGER DEFAULT 0,
                    categoria TEXT NOT NULL,
                    tema_especifico TEXT NOT NULL,
                    titulo_jornalistico TEXT NOT NULL,
                    titulo_normalizado TEXT NOT NULL,
                    resumo_3_linhas TEXT NOT NULL,
                    descricao_detalhada TEXT NOT NULL,
                    fonte_principal TEXT NOT NULL,
                    url_original TEXT NOT NULL UNIQUE,
                    veiculo_instituicao TEXT NOT NULL,
                    autor TEXT DEFAULT '',
                    data_publicacao TEXT DEFAULT '',
                    data_acontecimento TEXT DEFAULT '',
                    data_hora_coleta TEXT NOT NULL,
                    fontes_complementares TEXT DEFAULT '',
                    urls_complementares TEXT DEFAULT '',
                    evidencias_verificadas TEXT NOT NULL,
                    angulo_jornalistico TEXT NOT NULL,
                    publico_afetado TEXT NOT NULL,
                    bairros_locais_instituicoes TEXT NOT NULL,
                    formato_sugerido TEXT NOT NULL,
                    nivel_verificacao TEXT NOT NULL,
                    informacoes_pendentes TEXT DEFAULT '',
                    proxima_acao_recomendada TEXT DEFAULT '',
                    situacao_editorial TEXT NOT NULL,
                    id_pauta_relacionada TEXT DEFAULT '',
                    identificadores_documentos TEXT DEFAULT '',
                    detalhes_denuncia_json TEXT DEFAULT '{}',
                    motivo_selecao TEXT DEFAULT '',
                    imagem_capa TEXT DEFAULT '',
                    id_execucao_origem TEXT NOT NULL,
                    id_execucao_ultima_atualizacao TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pncp_contratos (
                    numero_controle_pncp TEXT PRIMARY KEY,
                    orgao_cnpj TEXT NOT NULL,
                    orgao_sigla TEXT NOT NULL,
                    orgao_nome TEXT NOT NULL,
                    unidade_nome TEXT DEFAULT '',
                    ano_contrato INTEGER NOT NULL,
                    numero_contrato TEXT DEFAULT '',
                    processo TEXT DEFAULT '',
                    tipo_contrato TEXT DEFAULT '',
                    fornecedor_cnpj TEXT DEFAULT '',
                    fornecedor_nome TEXT DEFAULT '',
                    objeto TEXT DEFAULT '',
                    valor_inicial REAL DEFAULT 0.0,
                    valor_global REAL DEFAULT 0.0,
                    valor_acumulado REAL DEFAULT 0.0,
                    data_assinatura TEXT DEFAULT '',
                    data_publicacao TEXT DEFAULT '',
                    data_vigencia_inicio TEXT DEFAULT '',
                    data_vigencia_fim TEXT DEFAULT '',
                    url_pncp TEXT DEFAULT '',
                    atualizado_em TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_pncp_ano ON pncp_contratos(ano_contrato);
                CREATE INDEX IF NOT EXISTS idx_pncp_orgao ON pncp_contratos(orgao_cnpj);
                CREATE INDEX IF NOT EXISTS idx_pncp_pub ON pncp_contratos(data_publicacao DESC);

                CREATE TABLE IF NOT EXISTS pncp_retificacoes (
                    numero_controle_pncp TEXT PRIMARY KEY,
                    valor_original_pncp REAL NOT NULL DEFAULT 0.0,
                    valor_corrigido REAL NOT NULL,
                    justificativa TEXT DEFAULT '',
                    atualizado_em TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pncp_ignorados (
                    numero_controle_pncp TEXT PRIMARY KEY,
                    motivo TEXT NOT NULL DEFAULT 'Contrato duplicado no PNCP',
                    atualizado_em TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidencias (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_pauta TEXT NOT NULL,
                    tipo_evidencia TEXT NOT NULL,
                    descricao TEXT NOT NULL,
                    referencia_url_ou_doc TEXT NOT NULL,
                    data_registro TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS historico_atualizacoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_pauta TEXT NOT NULL,
                    id_execucao TEXT NOT NULL,
                    data_atualizacao TEXT NOT NULL,
                    tipo_mudanca TEXT NOT NULL,
                    descricao_mudanca TEXT NOT NULL,
                    estado_anterior_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pendencias_apuracao (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_pauta TEXT NOT NULL UNIQUE,
                    questao_pendente TEXT NOT NULL,
                    documento_necessario TEXT NOT NULL,
                    instituicao_ou_pessoa_a_consultar TEXT NOT NULL,
                    evidencia_ja_disponivel TEXT NOT NULL,
                    proxima_acao TEXT NOT NULL,
                    status_apuracao TEXT NOT NULL,
                    data_atualizacao TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS itens_descartados (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_execucao TEXT NOT NULL,
                    url TEXT NOT NULL,
                    titulo TEXT NOT NULL,
                    motivo_descarte TEXT NOT NULL,
                    data_registro TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS erros_coleta (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    id_execucao TEXT NOT NULL,
                    componente TEXT NOT NULL,
                    alvo_url TEXT NOT NULL,
                    mensagem_erro TEXT NOT NULL,
                    data_registro TEXT NOT NULL
                );
                """
            )
            # Migração segura para bancos pré-existentes sem a coluna imagem_capa
            cols = {r[1] for r in conn.execute("PRAGMA table_info(pautas)").fetchall()}
            if "imagem_capa" not in cols:
                conn.execute("ALTER TABLE pautas ADD COLUMN imagem_capa TEXT DEFAULT ''")

    def recover_interrupted_executions(self) -> int:
        """Marca execuções anteriores que ficaram presas em EM_ANDAMENTO como INTERROMPIDA_RECUPERADA."""
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE execucoes
                SET situacao_execucao = 'INTERROMPIDA_RECUPERADA'
                WHERE situacao_execucao = 'EM_ANDAMENTO'
                """
            )
            return cur.rowcount

    def start_execution(self, id_execucao: str, modo: str = "manual") -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO execucoes (id_execucao, data_horario, modo, situacao_execucao)
                VALUES (?, ?, ?, 'EM_ANDAMENTO')
                """,
                (id_execucao, now_iso, modo),
            )

    def finish_execution(
        self,
        id_execucao: str,
        fontes_consultadas: int,
        fontes_com_falha: int,
        paginas_lidas: int,
        pautas_novas: int,
        pautas_atualizadas: int,
        duplicidades_eliminadas: int,
        itens_descartados: int,
        duracao_segundos: float,
        situacao_execucao: str = "CONCLUIDA",
        resumo: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE execucoes
                SET fontes_consultadas = ?,
                    fontes_com_falha = ?,
                    paginas_lidas = ?,
                    pautas_novas = ?,
                    pautas_atualizadas = ?,
                    duplicidades_eliminadas = ?,
                    itens_descartados = ?,
                    duracao_segundos = ?,
                    situacao_execucao = ?,
                    resumo_json = ?
                WHERE id_execucao = ?
                """,
                (
                    fontes_consultadas,
                    fontes_com_falha,
                    paginas_lidas,
                    pautas_novas,
                    pautas_atualizadas,
                    duplicidades_eliminadas,
                    itens_descartados,
                    round(duracao_segundos, 2),
                    situacao_execucao,
                    json.dumps(resumo or {}, ensure_ascii=False),
                    id_execucao,
                ),
            )

    def upsert_fonte(self, fonte: Dict[str, str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO fontes (
                    nome, tipo, url, prioridade, temas,
                    situacao_funcionamento, ultima_consulta, ultima_coleta_sucesso,
                    metodo_acesso, observacoes_limitacoes, erro_identificado, proxima_acao_recomendada
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    nome = excluded.nome,
                    tipo = excluded.tipo,
                    prioridade = excluded.prioridade,
                    temas = excluded.temas,
                    situacao_funcionamento = CASE
                        WHEN excluded.situacao_funcionamento != '' THEN excluded.situacao_funcionamento
                        ELSE fontes.situacao_funcionamento
                    END,
                    ultima_consulta = CASE
                        WHEN excluded.ultima_consulta != '' THEN excluded.ultima_consulta
                        ELSE fontes.ultima_consulta
                    END,
                    ultima_coleta_sucesso = CASE
                        WHEN excluded.ultima_coleta_sucesso != '' THEN excluded.ultima_coleta_sucesso
                        ELSE fontes.ultima_coleta_sucesso
                    END,
                    metodo_acesso = CASE
                        WHEN excluded.metodo_acesso != '' THEN excluded.metodo_acesso
                        ELSE fontes.metodo_acesso
                    END,
                    observacoes_limitacoes = CASE
                        WHEN excluded.observacoes_limitacoes != '' THEN excluded.observacoes_limitacoes
                        ELSE fontes.observacoes_limitacoes
                    END,
                    erro_identificado = excluded.erro_identificado,
                    proxima_acao_recomendada = CASE
                        WHEN excluded.proxima_acao_recomendada != '' THEN excluded.proxima_acao_recomendada
                        ELSE fontes.proxima_acao_recomendada
                    END
                """,
                (
                    fonte.get("nome", ""),
                    fonte.get("tipo", ""),
                    fonte.get("url", ""),
                    fonte.get("prioridade", "Média"),
                    fonte.get("temas", ""),
                    fonte.get("situacao_funcionamento", "Cadastrada"),
                    fonte.get("ultima_consulta", ""),
                    fonte.get("ultima_coleta_sucesso", ""),
                    fonte.get("metodo_acesso", ""),
                    fonte.get("observacoes_limitacoes", ""),
                    fonte.get("erro_identificado", ""),
                    fonte.get("proxima_acao_recomendada", ""),
                ),
            )

    def get_all_fontes(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM fontes ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]

    def get_fonte_by_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Busca uma fonte cadastrada pela URL exata."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM fontes WHERE url = ?", (url,)).fetchone()
            return dict(row) if row else None

    def log_error(
        self,
        id_execucao: str,
        componente: str,
        alvo_url: str,
        mensagem_erro: str,
        data_registro: Optional[str] = None,
    ) -> None:
        """Persiste falha operacional ou de rede na tabela erros_coleta."""
        dt = data_registro or datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO erros_coleta (id_execucao, componente, alvo_url, mensagem_erro, data_registro)
                VALUES (?, ?, ?, ?, ?)
                """,
                (id_execucao, componente, alvo_url, mensagem_erro, dt),
            )

    def get_errors_for_execution(self, id_execucao: str) -> List[Dict[str, Any]]:
        """Lista os erros registrados em uma execução específica."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM erros_coleta WHERE id_execucao = ? ORDER BY id ASC",
                (id_execucao,),
            ).fetchall()
            return [dict(r) for r in rows]

    def log_collected_page(
        self,
        id_execucao: str,
        url: str,
        fonte_nome: str,
        titulo: str,
        data_publicacao: str,
        data_acontecimento: str,
        metodo_coleta: str,
        status_leitura: str,
        conteudo_hash: str,
        conteudo_extraido: str,
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO paginas_coletadas (
                    id_execucao, url, fonte_nome, titulo, data_publicacao,
                    data_acontecimento, data_coleta, metodo_coleta, status_leitura,
                    conteudo_hash, conteudo_extraido
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    id_execucao,
                    url,
                    fonte_nome,
                    titulo,
                    data_publicacao,
                    data_acontecimento,
                    now_iso,
                    metodo_coleta,
                    status_leitura,
                    conteudo_hash,
                    conteudo_extraido,
                ),
            )

    def get_all_pautas(self, order_by: str = "recentes") -> List[Dict[str, Any]]:
        if order_by == "score":
            sql = (
                "SELECT * FROM pautas "
                "ORDER BY pontuacao_editorial DESC, COALESCE(NULLIF(data_publicacao, ''), substr(data_hora_coleta, 1, 10)) DESC, id_pauta ASC"
            )
        else:
            sql = (
                "SELECT * FROM pautas "
                "ORDER BY COALESCE(NULLIF(data_publicacao, ''), substr(data_hora_coleta, 1, 10)) DESC, pontuacao_editorial DESC, id_pauta ASC"
            )
        with self.connect() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def get_pautas_by_execution(self, id_execucao: str) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pautas
                WHERE id_execucao_origem = ?
                ORDER BY pontuacao_editorial DESC, id_pauta ASC
                """,
                (id_execucao,),
            ).fetchall()
            return [dict(r) for r in rows]

    def insert_pauta(self, pauta: Dict[str, Any], id_execucao: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pautas (
                    id_pauta, prioridade, pontuacao_editorial, justificativa_pontuacao,
                    emergencia_sinalizada, categoria, tema_especifico, titulo_jornalistico,
                    titulo_normalizado, resumo_3_linhas, descricao_detalhada,
                    fonte_principal, url_original, veiculo_instituicao, autor,
                    data_publicacao, data_acontecimento, data_hora_coleta,
                    fontes_complementares, urls_complementares, evidencias_verificadas,
                    angulo_jornalistico, publico_afetado, bairros_locais_instituicoes,
                    formato_sugerido, nivel_verificacao, informacoes_pendentes,
                    proxima_acao_recomendada, situacao_editorial, id_pauta_relacionada,
                    identificadores_documentos, detalhes_denuncia_json, motivo_selecao,
                    imagem_capa, id_execucao_origem, id_execucao_ultima_atualizacao
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    pauta.get("id_pauta", ""),
                    pauta.get("prioridade", "Média"),
                    int(pauta.get("pontuacao_editorial", 50)),
                    pauta.get("justificativa_pontuacao", ""),
                    1 if pauta.get("emergencia_sinalizada") else 0,
                    pauta.get("categoria", "Pautas de serviço público"),
                    pauta.get("tema_especifico", "Geral"),
                    pauta.get("titulo_jornalistico", ""),
                    pauta.get("titulo_normalizado", ""),
                    pauta.get("resumo_3_linhas", ""),
                    pauta.get("descricao_detalhada", ""),
                    pauta.get("fonte_principal", ""),
                    pauta.get("url_original", ""),
                    pauta.get("veiculo_instituicao", ""),
                    pauta.get("autor", ""),
                    pauta.get("data_publicacao", ""),
                    pauta.get("data_acontecimento", ""),
                    pauta.get("data_hora_coleta", datetime.now().isoformat(timespec="seconds")),
                    pauta.get("fontes_complementares", ""),
                    pauta.get("urls_complementares", ""),
                    pauta.get("evidencias_verificadas", ""),
                    pauta.get("angulo_jornalistico", ""),
                    pauta.get("publico_afetado", ""),
                    pauta.get("bairros_locais_instituicoes", ""),
                    pauta.get("formato_sugerido", "Matéria jornalística"),
                    pauta.get("nivel_verificacao", "Parcialmente confirmado"),
                    pauta.get("informacoes_pendentes", ""),
                    pauta.get("proxima_acao_recomendada", ""),
                    pauta.get("situacao_editorial", "Sugerida"),
                    pauta.get("id_pauta_relacionada", ""),
                    pauta.get("identificadores_documentos", ""),
                    json.dumps(pauta.get("detalhes_denuncia", {}), ensure_ascii=False),
                    pauta.get("motivo_selecao", ""),
                    pauta.get("imagem_capa", ""),
                    id_execucao,
                    id_execucao,
                ),
            )

    def update_pauta_imagem_capa(self, id_pauta: str, imagem_capa: str) -> None:
        """Preenche imagem_capa de uma pauta existente quando ainda vazia."""
        if not imagem_capa:
            return
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE pautas
                SET imagem_capa = ?
                WHERE id_pauta = ? AND (imagem_capa IS NULL OR imagem_capa = '')
                """,
                (imagem_capa, id_pauta),
            )

    def update_existing_pauta(
        self,
        id_pauta: str,
        updates: Dict[str, Any],
        id_execucao: str,
        tipo_mudanca: str,
        descricao_mudanca: str,
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            old_row = conn.execute(
                "SELECT * FROM pautas WHERE id_pauta = ?", (id_pauta,)
            ).fetchone()
            if not old_row:
                return
            old_dict = dict(old_row)
            conn.execute(
                """
                INSERT INTO historico_atualizacoes (
                    id_pauta, id_execucao, data_atualizacao, tipo_mudanca,
                    descricao_mudanca, estado_anterior_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    id_pauta,
                    id_execucao,
                    now_iso,
                    tipo_mudanca,
                    descricao_mudanca,
                    json.dumps(old_dict, ensure_ascii=False),
                ),
            )
            nova_capa = updates.get("imagem_capa") or old_dict.get("imagem_capa", "")
            conn.execute(
                """
                UPDATE pautas
                SET fontes_complementares = ?,
                    urls_complementares = ?,
                    evidencias_verificadas = ?,
                    descricao_detalhada = ?,
                    pontuacao_editorial = ?,
                    prioridade = ?,
                    situacao_editorial = ?,
                    imagem_capa = ?,
                    id_execucao_ultima_atualizacao = ?
                WHERE id_pauta = ?
                """,
                (
                    updates.get("fontes_complementares", old_dict["fontes_complementares"]),
                    updates.get("urls_complementares", old_dict["urls_complementares"]),
                    updates.get("evidencias_verificadas", old_dict["evidencias_verificadas"]),
                    updates.get("descricao_detalhada", old_dict["descricao_detalhada"]),
                    int(updates.get("pontuacao_editorial", old_dict["pontuacao_editorial"])),
                    updates.get("prioridade", old_dict["prioridade"]),
                    updates.get("situacao_editorial", old_dict["situacao_editorial"]),
                    nova_capa,
                    id_execucao,
                    id_pauta,
                ),
            )

    def add_evidencia(
        self, id_pauta: str, tipo_evidencia: str, descricao: str, referencia: str
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO evidencias (id_pauta, tipo_evidencia, descricao, referencia_url_ou_doc, data_registro)
                VALUES (?, ?, ?, ?, ?)
                """,
                (id_pauta, tipo_evidencia, descricao, referencia, now_iso),
            )

    def upsert_pendencia(
        self,
        id_pauta: str,
        questao_pendente: str,
        documento_necessario: str,
        instituicao_ou_pessoa: str,
        evidencia_disponivel: str,
        proxima_acao: str,
        status_apuracao: str,
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO pendencias_apuracao (
                    id_pauta, questao_pendente, documento_necessario,
                    instituicao_ou_pessoa_a_consultar, evidencia_ja_disponivel,
                    proxima_acao, status_apuracao, data_atualizacao
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id_pauta) DO UPDATE SET
                    questao_pendente = excluded.questao_pendente,
                    documento_necessario = excluded.documento_necessario,
                    instituicao_ou_pessoa_a_consultar = excluded.instituicao_ou_pessoa_a_consultar,
                    evidencia_ja_disponivel = excluded.evidencia_ja_disponivel,
                    proxima_acao = excluded.proxima_acao,
                    status_apuracao = excluded.status_apuracao,
                    data_atualizacao = excluded.data_atualizacao
                """,
                (
                    id_pauta,
                    questao_pendente,
                    documento_necessario,
                    instituicao_ou_pessoa,
                    evidencia_disponivel,
                    proxima_acao,
                    status_apuracao,
                    now_iso,
                ),
            )

    def get_all_pendencias(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pendencias_apuracao ORDER BY id_pauta ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_pendencias_detalhadas(self) -> List[Dict[str, Any]]:
        """Retorna pendências enriquecidas com título, prioridade, fonte, link e capa da pauta."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT pa.*,
                       p.titulo_jornalistico,
                       p.prioridade,
                       p.pontuacao_editorial,
                       p.fonte_principal,
                       p.url_original,
                       p.data_publicacao,
                       p.nivel_verificacao,
                       p.imagem_capa
                FROM pendencias_apuracao pa
                LEFT JOIN pautas p ON pa.id_pauta = p.id_pauta
                ORDER BY COALESCE(NULLIF(p.data_publicacao, ''), pa.data_atualizacao) DESC,
                         COALESCE(p.pontuacao_editorial, 0) DESC,
                         pa.id_pauta ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_execucoes(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM execucoes ORDER BY data_horario ASC"
            ).fetchall()
            return [dict(r) for r in rows]

    def log_discarded_item(
        self, id_execucao: str, url: str, titulo: str, motivo: str
    ) -> None:
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO itens_descartados (id_execucao, url, titulo, motivo_descarte, data_registro)
                VALUES (?, ?, ?, ?, ?)
                """,
                (id_execucao, url, titulo, motivo, now_iso),
            )

    def get_discarded_items(self, id_execucao: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if id_execucao:
                rows = conn.execute(
                    "SELECT * FROM itens_descartados WHERE id_execucao = ? ORDER BY id ASC",
                    (id_execucao,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM itens_descartados ORDER BY id ASC"
                ).fetchall()
            return [dict(r) for r in rows]

    # =========================================================================
    # PERSISTÊNCIA E CONSULTA DE CONTRATOS DO PNCP (2024 - 2026)
    # =========================================================================

    def upsert_pncp_contrato(self, contrato: Dict[str, Any]) -> None:
        self.upsert_pncp_contratos_batch([contrato])

    def upsert_pncp_contratos_batch(self, contratos: List[Dict[str, Any]]) -> int:
        if not contratos:
            return 0
        now_iso = datetime.now().isoformat(timespec="seconds")
        sql = """
            INSERT INTO pncp_contratos (
                numero_controle_pncp, orgao_cnpj, orgao_sigla, orgao_nome,
                unidade_nome, ano_contrato, numero_contrato, processo,
                tipo_contrato, fornecedor_cnpj, fornecedor_nome, objeto,
                valor_inicial, valor_global, valor_acumulado,
                data_assinatura, data_publicacao, data_vigencia_inicio,
                data_vigencia_fim, url_pncp, atualizado_em
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(numero_controle_pncp) DO UPDATE SET
                orgao_sigla = excluded.orgao_sigla,
                orgao_nome = excluded.orgao_nome,
                unidade_nome = excluded.unidade_nome,
                ano_contrato = excluded.ano_contrato,
                numero_contrato = excluded.numero_contrato,
                processo = excluded.processo,
                tipo_contrato = excluded.tipo_contrato,
                fornecedor_cnpj = excluded.fornecedor_cnpj,
                fornecedor_nome = excluded.fornecedor_nome,
                objeto = excluded.objeto,
                valor_inicial = excluded.valor_inicial,
                valor_global = excluded.valor_global,
                valor_acumulado = excluded.valor_acumulado,
                data_assinatura = excluded.data_assinatura,
                data_publicacao = excluded.data_publicacao,
                data_vigencia_inicio = excluded.data_vigencia_inicio,
                data_vigencia_fim = excluded.data_vigencia_fim,
                url_pncp = excluded.url_pncp,
                atualizado_em = excluded.atualizado_em
        """
        params = [
            (
                str(c.get("numero_controle_pncp", "")),
                str(c.get("orgao_cnpj", "")),
                str(c.get("orgao_sigla", "")),
                str(c.get("orgao_nome", "")),
                str(c.get("unidade_nome", "")),
                int(c.get("ano_contrato", 2026) or 2026),
                str(c.get("numero_contrato", "")),
                str(c.get("processo", "")),
                str(c.get("tipo_contrato", "")),
                str(c.get("fornecedor_cnpj", "")),
                str(c.get("fornecedor_nome", "")),
                str(c.get("objeto", "")),
                float(c.get("valor_inicial", 0.0) or 0.0),
                float(c.get("valor_global", 0.0) or 0.0),
                float(c.get("valor_acumulado", 0.0) or 0.0),
                str(c.get("data_assinatura", "")),
                str(c.get("data_publicacao", "")),
                str(c.get("data_vigencia_inicio", "")),
                str(c.get("data_vigencia_fim", "")),
                str(c.get("url_pncp", "")),
                str(c.get("atualizado_em") or now_iso),
            )
            for c in contratos
            if c.get("numero_controle_pncp")
        ]
        if not params:
            return 0
        with self.connect() as conn:
            conn.executemany(sql, params)
            # Reaplica automaticamente quaisquer retificações manuais salvas pela redação
            conn.execute(
                """
                UPDATE pncp_contratos
                SET valor_inicial = (
                        SELECT r.valor_corrigido FROM pncp_retificacoes r
                        WHERE r.numero_controle_pncp = pncp_contratos.numero_controle_pncp
                    ),
                    valor_global = (
                        SELECT r.valor_corrigido FROM pncp_retificacoes r
                        WHERE r.numero_controle_pncp = pncp_contratos.numero_controle_pncp
                    ),
                    valor_acumulado = (
                        SELECT r.valor_corrigido FROM pncp_retificacoes r
                        WHERE r.numero_controle_pncp = pncp_contratos.numero_controle_pncp
                    )
                WHERE numero_controle_pncp IN (SELECT numero_controle_pncp FROM pncp_retificacoes)
                """
            )
        return len(params)

    @staticmethod
    def _format_pncp_row(row: sqlite3.Row) -> Dict[str, Any]:
        """Converte linha PNCP enriquecida com JOIN de retificações, ignorados e categoria temática."""
        d = dict(row)
        d["retificado"] = bool(d.pop("retificado_flag", 0))
        if d.get("valor_original_pncp") is not None:
            d["valor_original_pncp"] = float(d["valor_original_pncp"])
        else:
            d["valor_original_pncp"] = None
        d["justificativa_retificacao"] = str(d.get("justificativa_retificacao") or "")
        d["ignorado"] = bool(d.pop("ignorado_flag", 0))
        d["motivo_ignorado"] = str(d.get("motivo_ignorado") or "")
        d["is_cpf"] = is_fornecedor_cpf(str(d.get("fornecedor_cnpj") or ""))
        d["categoria_tematica"] = classify_pncp_contrato(d)
        return d

    def _select_pncp_single_row(
        self, conn: sqlite3.Connection, numero_controle_pncp: str
    ) -> Optional[Dict[str, Any]]:
        row = conn.execute(
            """
            SELECT c.*,
                   CASE WHEN r.valor_corrigido IS NOT NULL THEN 1 ELSE 0 END AS retificado_flag,
                   r.valor_original_pncp,
                   COALESCE(r.justificativa, '') AS justificativa_retificacao,
                   CASE WHEN ig.numero_controle_pncp IS NOT NULL THEN 1 ELSE 0 END AS ignorado_flag,
                   COALESCE(ig.motivo, '') AS motivo_ignorado
            FROM pncp_contratos c
            LEFT JOIN pncp_retificacoes r ON c.numero_controle_pncp = r.numero_controle_pncp
            LEFT JOIN pncp_ignorados ig ON c.numero_controle_pncp = ig.numero_controle_pncp
            WHERE c.numero_controle_pncp = ?
            """,
            (numero_controle_pncp,),
        ).fetchone()
        return self._format_pncp_row(row) if row else None

    def retificar_pncp_contrato(
        self,
        numero_controle_pncp: str,
        valor_corrigido: float,
        justificativa: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Retifica manualmente o valor de um contrato PNCP preservando o valor original para auditoria."""
        ctrl = (numero_controle_pncp or "").strip()
        val = float(valor_corrigido)
        if not ctrl or val <= 0:
            return None
        now_iso = datetime.now().isoformat(timespec="seconds")
        just_clean = (justificativa or "").strip()[:500]
        with self.connect() as conn:
            cur_row = conn.execute(
                "SELECT valor_global FROM pncp_contratos WHERE numero_controle_pncp = ?",
                (ctrl,),
            ).fetchone()
            if not cur_row:
                return None

            existing_ret = conn.execute(
                "SELECT valor_original_pncp FROM pncp_retificacoes WHERE numero_controle_pncp = ?",
                (ctrl,),
            ).fetchone()
            orig_val = (
                float(existing_ret["valor_original_pncp"])
                if existing_ret
                else float(cur_row["valor_global"] or 0.0)
            )
            conn.execute(
                """
                INSERT INTO pncp_retificacoes (
                    numero_controle_pncp, valor_original_pncp, valor_corrigido, justificativa, atualizado_em
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(numero_controle_pncp) DO UPDATE SET
                    valor_corrigido = excluded.valor_corrigido,
                    justificativa = excluded.justificativa,
                    atualizado_em = excluded.atualizado_em
                """,
                (ctrl, orig_val, val, just_clean, now_iso),
            )
            conn.execute(
                """
                UPDATE pncp_contratos
                SET valor_inicial = ?, valor_global = ?, valor_acumulado = ?, atualizado_em = ?
                WHERE numero_controle_pncp = ?
                """,
                (val, val, val, now_iso, ctrl),
            )
            return self._select_pncp_single_row(conn, ctrl)

    def restaurar_pncp_contrato(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        """Restaura o valor original publicado no PNCP removendo a retificação local."""
        ctrl = (numero_controle_pncp or "").strip()
        if not ctrl:
            return None
        now_iso = datetime.now().isoformat(timespec="seconds")
        with self.connect() as conn:
            ret_row = conn.execute(
                "SELECT valor_original_pncp FROM pncp_retificacoes WHERE numero_controle_pncp = ?",
                (ctrl,),
            ).fetchone()
            if not ret_row:
                return None
            orig_val = float(ret_row["valor_original_pncp"] or 0.0)
            conn.execute(
                """
                UPDATE pncp_contratos
                SET valor_inicial = ?, valor_global = ?, valor_acumulado = ?, atualizado_em = ?
                WHERE numero_controle_pncp = ?
                """,
                (orig_val, orig_val, orig_val, now_iso, ctrl),
            )
            conn.execute(
                "DELETE FROM pncp_retificacoes WHERE numero_controle_pncp = ?",
                (ctrl,),
            )
            return self._select_pncp_single_row(conn, ctrl)

    def get_all_pncp_retificacoes(self) -> List[Dict[str, Any]]:
        """Lista todas as retificações aplicadas aos contratos do PNCP."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pncp_retificacoes ORDER BY atualizado_em DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def apply_pncp_retificacoes_batch(self, retificacoes: List[Dict[str, Any]]) -> int:
        """Aplica retificações em lote (ex.: reidratação em ambiente serverless)."""
        if not retificacoes:
            return 0
        applied = 0
        for item in retificacoes:
            if not isinstance(item, dict):
                continue
            ctrl = str(item.get("numero_controle_pncp") or "").strip()
            just = str(item.get("justificativa") or item.get("justificativa_retificacao") or "").strip()
            try:
                val = float(item.get("valor_corrigido") or 0.0)
            except (TypeError, ValueError):
                val = 0.0
            if ctrl and val > 0:
                if self.retificar_pncp_contrato(ctrl, val, just):
                    applied += 1
        return applied

    def ignorar_pncp_contrato(
        self, numero_controle_pncp: str, motivo: str = "Contrato duplicado no PNCP"
    ) -> Optional[Dict[str, Any]]:
        """Marca um contrato do PNCP como ignorado/duplicado sem apagá-lo da memória histórica."""
        ctrl = (numero_controle_pncp or "").strip()
        if not ctrl:
            return None
        now_iso = datetime.now().isoformat(timespec="seconds")
        motivo_clean = (motivo or "Contrato duplicado no PNCP").strip()[:240] or "Contrato duplicado no PNCP"
        with self.connect() as conn:
            cur_row = conn.execute(
                "SELECT numero_controle_pncp FROM pncp_contratos WHERE numero_controle_pncp = ?",
                (ctrl,),
            ).fetchone()
            if not cur_row:
                return None
            conn.execute(
                """
                INSERT INTO pncp_ignorados (numero_controle_pncp, motivo, atualizado_em)
                VALUES (?, ?, ?)
                ON CONFLICT(numero_controle_pncp) DO UPDATE SET
                    motivo = excluded.motivo,
                    atualizado_em = excluded.atualizado_em
                """,
                (ctrl, motivo_clean, now_iso),
            )
            return self._select_pncp_single_row(conn, ctrl)

    def restaurar_pncp_ignorado(self, numero_controle_pncp: str) -> Optional[Dict[str, Any]]:
        """Remove a marcação de ignorado/duplicado devolvendo o contrato aos totais."""
        ctrl = (numero_controle_pncp or "").strip()
        if not ctrl:
            return None
        with self.connect() as conn:
            cur_row = conn.execute(
                "SELECT numero_controle_pncp FROM pncp_contratos WHERE numero_controle_pncp = ?",
                (ctrl,),
            ).fetchone()
            if not cur_row:
                return None
            conn.execute(
                "DELETE FROM pncp_ignorados WHERE numero_controle_pncp = ?",
                (ctrl,),
            )
            return self._select_pncp_single_row(conn, ctrl)

    def get_all_pncp_ignorados(self) -> List[Dict[str, Any]]:
        """Lista todos os contratos do PNCP marcados como ignorados."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pncp_ignorados ORDER BY atualizado_em DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def sync_pncp_retificacoes_from_request(self, overrides: Dict[str, Any]) -> int:
        """Sincroniza retificações e ignorados enviados por header/cookie em uma única transação."""
        if not overrides or not isinstance(overrides, dict):
            return 0

        entries: List[tuple[str, Dict[str, Any]]] = []
        for raw_ctrl, entry in list(overrides.items())[:100]:
            ctrl = str(raw_ctrl or "").strip()
            if ctrl and isinstance(entry, dict):
                entries.append((ctrl, entry))
        if not entries:
            return 0

        now_iso = datetime.now().isoformat(timespec="seconds")
        ctrls = [ctrl for ctrl, _ in entries]
        placeholders = ",".join("?" for _ in ctrls)
        applied = 0

        with self.connect() as conn:
            ret_rows = conn.execute(
                f"""
                SELECT numero_controle_pncp, valor_original_pncp, valor_corrigido, justificativa
                FROM pncp_retificacoes
                WHERE numero_controle_pncp IN ({placeholders})
                """,
                ctrls,
            ).fetchall()
            existing_ret = {r["numero_controle_pncp"]: r for r in ret_rows}

            ign_rows = conn.execute(
                f"""
                SELECT numero_controle_pncp, motivo
                FROM pncp_ignorados
                WHERE numero_controle_pncp IN ({placeholders})
                """,
                ctrls,
            ).fetchall()
            existing_ign = {r["numero_controle_pncp"]: r for r in ign_rows}

            contract_rows = conn.execute(
                f"""
                SELECT numero_controle_pncp, valor_global
                FROM pncp_contratos
                WHERE numero_controle_pncp IN ({placeholders})
                """,
                ctrls,
            ).fetchall()
            existing_contracts = {r["numero_controle_pncp"]: r for r in contract_rows}

            for ctrl, entry in entries:
                contract_row = existing_contracts.get(ctrl)
                if contract_row is None:
                    continue

                if entry.get("i") or entry.get("ignorar"):
                    motivo_clean = (
                        str(
                            entry.get("m")
                            or entry.get("motivo")
                            or entry.get("motivo_ignorado")
                            or "Contrato duplicado no PNCP"
                        ).strip()[:160]
                        or "Contrato duplicado no PNCP"
                    )
                    cur_ign = existing_ign.get(ctrl)
                    if cur_ign is None or str(cur_ign["motivo"] or "") != motivo_clean:
                        conn.execute(
                            """
                            INSERT INTO pncp_ignorados (numero_controle_pncp, motivo, atualizado_em)
                            VALUES (?, ?, ?)
                            ON CONFLICT(numero_controle_pncp) DO UPDATE SET
                                motivo = excluded.motivo,
                                atualizado_em = excluded.atualizado_em
                            """,
                            (ctrl, motivo_clean, now_iso),
                        )
                        existing_ign[ctrl] = {"numero_controle_pncp": ctrl, "motivo": motivo_clean}
                        applied += 1
                elif entry.get("u") or entry.get("designorar"):
                    if ctrl in existing_ign:
                        conn.execute(
                            "DELETE FROM pncp_ignorados WHERE numero_controle_pncp = ?",
                            (ctrl,),
                        )
                        existing_ign.pop(ctrl, None)
                        applied += 1

                if entry.get("r") or entry.get("restaurar"):
                    ret_row = existing_ret.pop(ctrl, None)
                    if ret_row is not None:
                        orig_val = float(ret_row["valor_original_pncp"] or 0.0)
                        conn.execute(
                            """
                            UPDATE pncp_contratos
                            SET valor_inicial = ?, valor_global = ?, valor_acumulado = ?, atualizado_em = ?
                            WHERE numero_controle_pncp = ?
                            """,
                            (orig_val, orig_val, orig_val, now_iso, ctrl),
                        )
                        conn.execute(
                            "DELETE FROM pncp_retificacoes WHERE numero_controle_pncp = ?",
                            (ctrl,),
                        )
                        applied += 1
                    continue

                raw_val = entry["v"] if "v" in entry else entry.get("valor_corrigido")
                try:
                    val = round(float(raw_val), 2)
                except (TypeError, ValueError):
                    continue
                if val <= 0:
                    continue

                just_clean = str(
                    entry.get("j")
                    or entry.get("justificativa")
                    or entry.get("justificativa_retificacao")
                    or ""
                ).strip()[:240]

                cur_ret = existing_ret.get(ctrl)
                if cur_ret is not None:
                    same_val = abs(float(cur_ret["valor_corrigido"] or 0.0) - val) < 0.005
                    same_just = str(cur_ret["justificativa"] or "") == just_clean
                    same_contract_val = abs(float(contract_row["valor_global"] or 0.0) - val) < 0.005
                    if same_val and same_just and same_contract_val:
                        continue

                orig_val = (
                    float(cur_ret["valor_original_pncp"] or 0.0)
                    if cur_ret is not None
                    else float(contract_row["valor_global"] or 0.0)
                )
                conn.execute(
                    """
                    INSERT INTO pncp_retificacoes (
                        numero_controle_pncp, valor_original_pncp, valor_corrigido, justificativa, atualizado_em
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(numero_controle_pncp) DO UPDATE SET
                        valor_corrigido = excluded.valor_corrigido,
                        justificativa = excluded.justificativa,
                        atualizado_em = excluded.atualizado_em
                    """,
                    (ctrl, orig_val, val, just_clean, now_iso),
                )
                conn.execute(
                    """
                    UPDATE pncp_contratos
                    SET valor_inicial = ?, valor_global = ?, valor_acumulado = ?, atualizado_em = ?
                    WHERE numero_controle_pncp = ?
                    """,
                    (val, val, val, now_iso, ctrl),
                )
                applied += 1

        return applied

    @staticmethod
    def _build_pncp_where(
        ano: Optional[int] = None,
        orgao: Optional[str] = None,
        fornecedor: Optional[str] = None,
        q: Optional[str] = None,
        unidade: Optional[str] = None,
        incluir_ignorados: bool = False,
        somente_ignorados: bool = False,
    ) -> tuple[str, List[Any]]:
        clauses: List[str] = []
        args: List[Any] = []
        if somente_ignorados:
            clauses.append(
                "EXISTS (SELECT 1 FROM pncp_ignorados ig_f WHERE ig_f.numero_controle_pncp = c.numero_controle_pncp)"
            )
        elif not incluir_ignorados:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM pncp_ignorados ig_f WHERE ig_f.numero_controle_pncp = c.numero_controle_pncp)"
            )
        if ano:
            clauses.append(
                "(c.ano_contrato = ? OR substr(c.data_publicacao, 1, 4) = ?)"
            )
            args.extend([int(ano), str(int(ano))])
        if orgao and orgao.lower() not in ("todos", "all", ""):
            org_clean = orgao.strip()
            clauses.append("(UPPER(c.orgao_sigla) = UPPER(?) OR c.orgao_cnpj = ?)")
            args.extend([org_clean, org_clean])
        if fornecedor and fornecedor.strip():
            f_clean = fornecedor.strip()
            clauses.append("(c.fornecedor_cnpj = ? OR UPPER(c.fornecedor_nome) LIKE UPPER(?))")
            args.extend([f_clean, f"%{f_clean}%"])
        if unidade and unidade.strip():
            clauses.append("UPPER(c.unidade_nome) LIKE UPPER(?)")
            args.append(f"%{unidade.strip()}%")
        if q and q.strip():
            like = f"%{q.strip()}%"
            clauses.append(
                "("
                "c.objeto LIKE ? OR "
                "c.fornecedor_nome LIKE ? OR "
                "c.fornecedor_cnpj LIKE ? OR "
                "c.numero_contrato LIKE ? OR "
                "c.numero_controle_pncp LIKE ? OR "
                "c.unidade_nome LIKE ? OR "
                "c.processo LIKE ? OR "
                "c.orgao_nome LIKE ?"
                ")"
            )
            args.extend([like, like, like, like, like, like, like, like])
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where_sql, args

    def query_pncp_contratos(
        self,
        ano: Optional[int] = None,
        orgao: Optional[str] = None,
        fornecedor: Optional[str] = None,
        q: Optional[str] = None,
        categoria: Optional[str] = None,
        unidade: Optional[str] = None,
        ordem: str = "recentes",
        pagina: int = 1,
        limite: int = 50,
        incluir_ignorados: bool = False,
        somente_ignorados: bool = False,
    ) -> Dict[str, Any]:
        pagina = max(1, int(pagina))
        limite = max(1, min(200, int(limite)))
        offset = (pagina - 1) * limite
        where_sql, args = self._build_pncp_where(
            ano=ano,
            orgao=orgao,
            fornecedor=fornecedor,
            q=q,
            unidade=unidade,
            incluir_ignorados=incluir_ignorados,
            somente_ignorados=somente_ignorados,
        )
        ordem_clean = (ordem or "recentes").strip().lower()
        if ordem_clean == "maior_valor":
            order_clause = "c.valor_global DESC, COALESCE(NULLIF(c.data_publicacao, ''), c.data_assinatura) DESC"
        elif ordem_clean == "menor_valor":
            order_clause = "c.valor_global ASC, COALESCE(NULLIF(c.data_publicacao, ''), c.data_assinatura) DESC"
        else:
            order_clause = "COALESCE(NULLIF(c.data_publicacao, ''), c.data_assinatura) DESC, c.ano_contrato DESC, c.valor_global DESC"

        cat_clean = (categoria or "").strip()
        filtrar_categoria = bool(cat_clean and cat_clean.lower() not in ("todas", "all"))

        with self.connect() as conn:
            ign_row = conn.execute("SELECT COUNT(*) AS cnt FROM pncp_ignorados").fetchone()
            total_ignorados = int(ign_row["cnt"]) if ign_row else 0

            if filtrar_categoria:
                rows = conn.execute(
                    f"""
                    SELECT c.*,
                           CASE WHEN r.valor_corrigido IS NOT NULL THEN 1 ELSE 0 END AS retificado_flag,
                           r.valor_original_pncp,
                           COALESCE(r.justificativa, '') AS justificativa_retificacao,
                           CASE WHEN ig.numero_controle_pncp IS NOT NULL THEN 1 ELSE 0 END AS ignorado_flag,
                           COALESCE(ig.motivo, '') AS motivo_ignorado
                    FROM pncp_contratos c
                    LEFT JOIN pncp_retificacoes r ON c.numero_controle_pncp = r.numero_controle_pncp
                    LEFT JOIN pncp_ignorados ig ON c.numero_controle_pncp = ig.numero_controle_pncp
                    {where_sql}
                    ORDER BY {order_clause}
                    """,
                    args,
                ).fetchall()
                filtrados = [
                    it
                    for it in (self._format_pncp_row(r) for r in rows)
                    if it["categoria_tematica"] == cat_clean
                ]
                total = len(filtrados)
                items_page = filtrados[offset : offset + limite]
            else:
                total_row = conn.execute(
                    f"SELECT COUNT(*) AS cnt FROM pncp_contratos c {where_sql}", args
                ).fetchone()
                total = int(total_row["cnt"]) if total_row else 0
                rows = conn.execute(
                    f"""
                    SELECT c.*,
                           CASE WHEN r.valor_corrigido IS NOT NULL THEN 1 ELSE 0 END AS retificado_flag,
                           r.valor_original_pncp,
                           COALESCE(r.justificativa, '') AS justificativa_retificacao,
                           CASE WHEN ig.numero_controle_pncp IS NOT NULL THEN 1 ELSE 0 END AS ignorado_flag,
                           COALESCE(ig.motivo, '') AS motivo_ignorado
                    FROM pncp_contratos c
                    LEFT JOIN pncp_retificacoes r ON c.numero_controle_pncp = r.numero_controle_pncp
                    LEFT JOIN pncp_ignorados ig ON c.numero_controle_pncp = ig.numero_controle_pncp
                    {where_sql}
                    ORDER BY {order_clause}
                    LIMIT ? OFFSET ?
                    """,
                    args + [limite, offset],
                ).fetchall()
                items_page = [self._format_pncp_row(r) for r in rows]

        total_paginas = max(1, (total + limite - 1) // limite)
        return {
            "items": items_page,
            "total": total,
            "pagina": pagina,
            "limite": limite,
            "total_paginas": total_paginas,
            "total_ignorados": total_ignorados,
        }

    def get_pncp_estatisticas(
        self, ano: Optional[int] = None, orgao: Optional[str] = None
    ) -> Dict[str, Any]:
        """Retorna estatísticas analíticas completas e classificação por tema dos contratos PNCP ativos."""
        where_sql, args = self._build_pncp_where(ano=ano, orgao=orgao)
        with self.connect() as conn:
            ign_row = conn.execute("SELECT COUNT(*) AS cnt FROM pncp_ignorados").fetchone()
            total_ignorados = int(ign_row["cnt"]) if ign_row else 0
            rows = conn.execute(
                f"""
                SELECT c.*,
                       CASE WHEN r.valor_corrigido IS NOT NULL THEN 1 ELSE 0 END AS retificado_flag,
                       r.valor_original_pncp,
                       COALESCE(r.justificativa, '') AS justificativa_retificacao,
                       0 AS ignorado_flag,
                       '' AS motivo_ignorado
                FROM pncp_contratos c
                LEFT JOIN pncp_retificacoes r ON c.numero_controle_pncp = r.numero_controle_pncp
                {where_sql}
                """,
                args,
            ).fetchall()
        formatted_rows = [self._format_pncp_row(r) for r in rows]
        est = build_pncp_estatisticas(formatted_rows)
        est["total_ignorados"] = total_ignorados
        if isinstance(est.get("resumo_geral"), dict):
            est["resumo_geral"]["contratos_ignorados"] = total_ignorados
        return est

    def get_pncp_resumo(
        self, ano: Optional[int] = None, orgao: Optional[str] = None
    ) -> Dict[str, Any]:
        """Agrega KPIs reais, série mensal, principais fornecedores e contratos recentes do PNCP."""
        where_sql, args = self._build_pncp_where(ano=ano, orgao=orgao)
        today = datetime.now().date()

        with self.connect() as conn:
            ign_row = conn.execute("SELECT COUNT(*) AS cnt FROM pncp_ignorados").fetchone()
            total_ignorados = int(ign_row["cnt"]) if ign_row else 0

            agg_row = conn.execute(
                f"""
                SELECT COUNT(*) AS total_contratos,
                       COALESCE(SUM(c.valor_global), 0.0) AS valor_total_global,
                       COALESCE(SUM(c.valor_inicial), 0.0) AS valor_total_inicial,
                       MAX(c.atualizado_em) AS ultima_sincronizacao
                FROM pncp_contratos c
                {where_sql}
                """,
                args,
            ).fetchone()

            total_contratos = int(agg_row["total_contratos"] or 0)
            valor_total_global = float(agg_row["valor_total_global"] or 0.0)
            valor_total_inicial = float(agg_row["valor_total_inicial"] or 0.0)
            ultima_sincronizacao = str(agg_row["ultima_sincronizacao"] or "")

            anos_rows = conn.execute(
                """
                SELECT DISTINCT c.ano_contrato
                FROM pncp_contratos c
                WHERE NOT EXISTS (
                    SELECT 1 FROM pncp_ignorados ig_f WHERE ig_f.numero_controle_pncp = c.numero_controle_pncp
                )
                ORDER BY c.ano_contrato DESC
                """
            ).fetchall()
            anos_disponiveis = [int(r["ano_contrato"]) for r in anos_rows if r["ano_contrato"]]

            # Totais por ano (respeitando filtro de órgão, se aplicado)
            org_where, org_args = self._build_pncp_where(ano=None, orgao=orgao)
            por_ano_rows = conn.execute(
                f"""
                SELECT c.ano_contrato,
                       COUNT(*) AS contratos,
                       COALESCE(SUM(c.valor_global), 0.0) AS valor_global
                FROM pncp_contratos c
                {org_where}
                GROUP BY c.ano_contrato
                ORDER BY c.ano_contrato DESC
                """,
                org_args,
            ).fetchall()
            por_ano = {
                str(r["ano_contrato"]): {
                    "contratos": int(r["contratos"]),
                    "valor_global": round(float(r["valor_global"] or 0.0), 2),
                }
                for r in por_ano_rows
            }

            # Totais por órgão
            por_orgao_rows = conn.execute(
                f"""
                SELECT c.orgao_cnpj, c.orgao_sigla, c.orgao_nome,
                       COUNT(*) AS contratos,
                       COALESCE(SUM(c.valor_global), 0.0) AS valor_global
                FROM pncp_contratos c
                {where_sql}
                GROUP BY c.orgao_cnpj, c.orgao_sigla, c.orgao_nome
                ORDER BY valor_global DESC
                """,
                args,
            ).fetchall()
            por_orgao = [
                {
                    "orgao_cnpj": r["orgao_cnpj"],
                    "orgao_sigla": r["orgao_sigla"],
                    "orgao_nome": r["orgao_nome"],
                    "contratos": int(r["contratos"]),
                    "valor_global": round(float(r["valor_global"] or 0.0), 2),
                }
                for r in por_orgao_rows
            ]

            # Evolução mensal (YYYY-MM) usando data_publicacao ou data_assinatura
            all_filtered_rows = conn.execute(
                f"""
                SELECT c.fornecedor_cnpj, c.fornecedor_nome, c.orgao_sigla,
                       c.valor_global, c.valor_inicial,
                       c.data_publicacao, c.data_assinatura,
                       c.data_vigencia_inicio, c.data_vigencia_fim, c.ano_contrato
                FROM pncp_contratos c
                {where_sql}
                """,
                args,
            ).fetchall()

            filtered_rows = [dict(r) for r in all_filtered_rows]

            ultimos_rows = conn.execute(
                f"""
                SELECT c.*,
                       CASE WHEN r.valor_corrigido IS NOT NULL THEN 1 ELSE 0 END AS retificado_flag,
                       r.valor_original_pncp,
                       COALESCE(r.justificativa, '') AS justificativa_retificacao,
                       0 AS ignorado_flag,
                       '' AS motivo_ignorado
                FROM pncp_contratos c
                LEFT JOIN pncp_retificacoes r ON c.numero_controle_pncp = r.numero_controle_pncp
                {where_sql}
                ORDER BY COALESCE(NULLIF(c.data_publicacao, ''), c.data_assinatura) DESC,
                         c.ano_contrato DESC,
                         c.valor_global DESC
                LIMIT 8
                """,
                args,
            ).fetchall()
            ultimos_contratos = [self._format_pncp_row(r) for r in ultimos_rows]

        return build_pncp_resumo_from_rows(
            filtered_rows=filtered_rows,
            anos_disponiveis=anos_disponiveis,
            today=today,
            por_orgao_override=por_orgao,
            por_ano_override=por_ano,
            ultimos_contratos_override=ultimos_contratos,
            agg_override={
                "total_contratos": total_contratos,
                "valor_total_global": valor_total_global,
                "valor_total_inicial": valor_total_inicial,
                "ultima_sincronizacao": ultima_sincronizacao,
                "total_ignorados": total_ignorados,
            },
        )


def build_pncp_resumo_from_rows(
    filtered_rows: List[Dict[str, Any]],
    org_rows: Optional[List[Dict[str, Any]]] = None,
    anos_disponiveis: Optional[List[int]] = None,
    today: Optional[date] = None,
    por_orgao_override: Optional[List[Dict[str, Any]]] = None,
    por_ano_override: Optional[Dict[str, Dict[str, Any]]] = None,
    ultimos_contratos_override: Optional[List[Dict[str, Any]]] = None,
    agg_override: Optional[Dict[str, Any]] = None,
    total_ignorados: int = 0,
) -> Dict[str, Any]:
    """Agrega KPIs, série mensal, fornecedores e contratos recentes de forma idêntica para SQLite e Supabase."""
    ref_today = today or datetime.now().date()

    if agg_override is not None:
        total_contratos = int(agg_override.get("total_contratos") or 0)
        valor_total_global = float(agg_override.get("valor_total_global") or 0.0)
        valor_total_inicial = float(agg_override.get("valor_total_inicial") or 0.0)
        ultima_sincronizacao = str(agg_override.get("ultima_sincronizacao") or "")
        ign_count = int(agg_override.get("total_ignorados") or total_ignorados or 0)
    else:
        total_contratos = len(filtered_rows)
        valor_total_global = sum(float(r.get("valor_global") or 0.0) for r in filtered_rows)
        valor_total_inicial = sum(float(r.get("valor_inicial") or 0.0) for r in filtered_rows)
        ultima_sincronizacao = max(
            (str(r.get("atualizado_em") or "") for r in filtered_rows),
            default="",
        )
        ign_count = int(total_ignorados or 0)

    if por_ano_override is not None:
        por_ano = por_ano_override
    else:
        source_ano = org_rows if org_rows is not None else filtered_rows
        ano_map: Dict[str, Dict[str, Any]] = {}
        for r in source_ano:
            yr = str(r.get("ano_contrato") or "")
            if not yr:
                continue
            if yr not in ano_map:
                ano_map[yr] = {"contratos": 0, "valor_global": 0.0}
            ano_map[yr]["contratos"] += 1
            ano_map[yr]["valor_global"] += float(r.get("valor_global") or 0.0)
        por_ano = {
            k: {
                "contratos": v["contratos"],
                "valor_global": round(v["valor_global"], 2),
            }
            for k, v in sorted(ano_map.items(), key=lambda x: x[0], reverse=True)
        }

    if por_orgao_override is not None:
        por_orgao = por_orgao_override
    else:
        org_map: Dict[str, Dict[str, Any]] = {}
        for r in filtered_rows:
            cnpj = str(r.get("orgao_cnpj") or "")
            sigla = str(r.get("orgao_sigla") or "")
            nome = str(r.get("orgao_nome") or "")
            okey = f"{cnpj}|{sigla}"
            if okey not in org_map:
                org_map[okey] = {
                    "orgao_cnpj": cnpj,
                    "orgao_sigla": sigla,
                    "orgao_nome": nome,
                    "contratos": 0,
                    "valor_global": 0.0,
                }
            org_map[okey]["contratos"] += 1
            org_map[okey]["valor_global"] += float(r.get("valor_global") or 0.0)
        por_orgao = [
            {
                "orgao_cnpj": v["orgao_cnpj"],
                "orgao_sigla": v["orgao_sigla"],
                "orgao_nome": v["orgao_nome"],
                "contratos": v["contratos"],
                "valor_global": round(v["valor_global"], 2),
            }
            for v in sorted(org_map.values(), key=lambda x: x["valor_global"], reverse=True)
        ]

    mensal_map: Dict[str, Dict[str, Any]] = {}
    forn_map: Dict[str, Dict[str, Any]] = {}

    for r in filtered_rows:
        ref_date = str(r.get("data_publicacao") or r.get("data_assinatura") or "").strip()
        mes_key = (
            ref_date[:7]
            if len(ref_date) >= 7 and ref_date[:4].isdigit()
            else f"{r.get('ano_contrato')}-01"
        )
        if mes_key not in mensal_map:
            mensal_map[mes_key] = {
                "mes": mes_key,
                "valor_global": 0.0,
                "valor_inicial": 0.0,
                "contratos": 0,
            }
        vg = float(r.get("valor_global") or 0.0)
        vi = float(r.get("valor_inicial") or 0.0)
        mensal_map[mes_key]["valor_global"] += vg
        mensal_map[mes_key]["valor_inicial"] += vi
        mensal_map[mes_key]["contratos"] += 1

        fnome = str(r.get("fornecedor_nome") or "").strip() or "Fornecedor não informado"
        fcnpj = str(r.get("fornecedor_cnpj") or "").strip()
        fkey = fcnpj or fnome.upper()
        if fkey not in forn_map:
            forn_map[fkey] = {
                "fornecedor_cnpj": fcnpj,
                "fornecedor_nome": fnome,
                "total_valor": 0.0,
                "qtd_contratos": 0,
                "orgaos": set(),
                "vigencias": [],
            }
        forn_map[fkey]["total_valor"] += vg
        forn_map[fkey]["qtd_contratos"] += 1
        if r.get("orgao_sigla"):
            forn_map[fkey]["orgaos"].add(str(r["orgao_sigla"]))

        vi_dt = str(r.get("data_vigencia_inicio") or "").strip()[:10]
        vf_dt = str(r.get("data_vigencia_fim") or "").strip()[:10]
        if len(vi_dt) == 10 and len(vf_dt) == 10:
            try:
                d_start = datetime.strptime(vi_dt, "%Y-%m-%d").date()
                d_end = datetime.strptime(vf_dt, "%Y-%m-%d").date()
                total_days = (d_end - d_start).days
                if total_days > 0:
                    elapsed = max(0, min(total_days, (ref_today - d_start).days))
                    forn_map[fkey]["vigencias"].append((elapsed / total_days) * 100.0)
            except ValueError:
                pass

    evolucao_mensal = [
        {
            "mes": k,
            "valor_global": round(v["valor_global"], 2),
            "valor_inicial": round(v["valor_inicial"], 2),
            "contratos": v["contratos"],
        }
        for k, v in sorted(mensal_map.items(), key=lambda x: x[0])
    ]

    sorted_forn = sorted(forn_map.values(), key=lambda x: x["total_valor"], reverse=True)[:8]
    top_fornecedores: List[Dict[str, Any]] = []
    for item in sorted_forn:
        vigs = item["vigencias"]
        vig_avg = round(sum(vigs) / len(vigs), 1) if vigs else None
        part_pct = (
            round((item["total_valor"] / valor_total_global) * 100.0, 1)
            if valor_total_global > 0
            else 0.0
        )
        top_fornecedores.append(
            {
                "fornecedor_cnpj": item["fornecedor_cnpj"],
                "fornecedor_nome": item["fornecedor_nome"],
                "total_valor": round(item["total_valor"], 2),
                "qtd_contratos": item["qtd_contratos"],
                "orgaos": ", ".join(sorted(item["orgaos"])),
                "participacao_pct": part_pct,
                "vigencia_media_pct": vig_avg,
            }
        )

    if ultimos_contratos_override is not None:
        ultimos_contratos = ultimos_contratos_override
    else:
        ultimos_contratos = sorted(
            filtered_rows,
            key=lambda c: (
                str(c.get("data_publicacao") or c.get("data_assinatura") or ""),
                int(c.get("ano_contrato") or 0),
                float(c.get("valor_global") or 0.0),
            ),
            reverse=True,
        )[:8]

    return {
        "total_contratos": total_contratos,
        "valor_total_global": round(valor_total_global, 2),
        "valor_total_inicial": round(valor_total_inicial, 2),
        "anos_disponiveis": anos_disponiveis or [2026, 2025, 2024],
        "por_ano": por_ano,
        "por_orgao": por_orgao,
        "evolucao_mensal": evolucao_mensal,
        "top_fornecedores": top_fornecedores,
        "ultimos_contratos": ultimos_contratos,
        "ultima_sincronizacao": ultima_sincronizacao,
        "total_ignorados": ign_count,
    }


def get_database(db_path: Path | None = None) -> Any:
    """Seleciona automaticamente o backend de persistência (`supabase` ou `sqlite`)."""
    forced = os.environ.get("RADAR_STORAGE_BACKEND", "auto").strip().lower()
    if forced == "sqlite":
        return RadarDatabase(db_path)
    if forced == "supabase" or (forced in ("", "auto") and is_supabase_configured()):
        from src.supabase_store import SupabaseDatabase

        return SupabaseDatabase.from_env()
    return RadarDatabase(db_path)
