"""
Módulo de Banco de Dados (SQLite) para persistência histórica de fontes, execuções,
páginas coletadas, pautas, evidências, atualizações, pendências e erros.
Não apaga registros antigos silenciosamente e preserva todo o histórico.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from src.config import DB_PATH


class RadarDatabase:
    """Gerencia a persistência relacional em SQLite do Radar de Pautas de Imperatriz."""

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
                    id_execucao_origem TEXT NOT NULL,
                    id_execucao_ultima_atualizacao TEXT NOT NULL
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

    def get_all_pautas(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pautas ORDER BY pontuacao_editorial DESC, id_pauta ASC"
            ).fetchall()
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
                    id_execucao_origem, id_execucao_ultima_atualizacao
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    id_execucao,
                    id_execucao,
                ),
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
            conn.execute(
                """
                UPDATE pautas
                SET font_comp_tmp = NULL
                WHERE 1=0
                """
            ) if False else None
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
