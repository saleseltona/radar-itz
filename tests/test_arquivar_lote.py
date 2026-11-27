"""
Testes do arquivamento em lote de pautas ("Arquivar tudo" do feed): SQLite, Supabase (cliente
falso) e rota HTTP com token. Herméticos: banco temporário, sem portas e sem rede.
"""
from __future__ import annotations

import email.message
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import src.web_server as web_server
from src.database import RadarDatabase, _ids_lote
from src.supabase_store import SupabaseDatabase
from src.web_server import ADMIN_TOKEN_ENV, LimiteTentativas, RadarAPIHandler

JSON = {"Content-Type": "application/json"}


def _pauta(pid: str) -> Dict[str, Any]:
    return {
        "id_pauta": pid, "prioridade": "Alta", "pontuacao_editorial": 80,
        "justificativa_pontuacao": "x", "categoria": "Pautas de serviço público", "tema_especifico": "Cidade",
        "titulo_jornalistico": f"Matéria {pid}", "titulo_normalizado": f"materia {pid.lower()}",
        "resumo_3_linhas": "r", "descricao_detalhada": "d", "fonte_principal": "PMI",
        "url_original": f"https://imperatriz.ma.gov.br/{pid.lower()}", "veiculo_instituicao": "PMI",
        "data_publicacao": "2026-11-20", "data_acontecimento": "2026-11-20", "evidencias_verificadas": "n",
        "angulo_jornalistico": "a", "publico_afetado": "p", "bairros_locais_instituicoes": "Centro",
        "formato_sugerido": "Matéria", "nivel_verificacao": "Confirmado", "situacao_editorial": "Pauta Inédita",
    }


class TestLoteSQLite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.tmp.name) / "lote.db")
        self.db.start_execution("EXEC-L", modo="manual")
        for pid in ("P-1", "P-2", "P-3"):
            self.db.insert_pauta(_pauta(pid), "EXEC-L")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def ativas(self) -> List[str]:
        return sorted(p["id_pauta"] for p in self.db.get_all_pautas(incluir_arquivadas=False))

    def test_arquiva_e_restaura_varias_de_uma_vez(self) -> None:
        feitas = self.db.arquivar_pautas_lote(["P-1", "P-2", "P-2", "NAO-EXISTE", ""], "Limpeza")
        self.assertEqual(feitas, ["P-1", "P-2"])
        self.assertEqual(self.ativas(), ["P-3"])
        self.assertEqual(self.db.restaurar_pautas_lote(["P-1", "P-2"]), ["P-1", "P-2"])
        self.assertEqual(self.ativas(), ["P-1", "P-2", "P-3"])

    def test_normaliza_e_limita_lote(self) -> None:
        self.assertEqual(_ids_lote("P-1"), [])
        self.assertEqual(_ids_lote([" a ", "a", None, "b"]), ["a", "b"])
        self.assertEqual(len(_ids_lote([f"x{i}" for i in range(500)])), 200)
        self.assertEqual(self.db.arquivar_pautas_lote([]), [])


class _ClienteFalso:
    def __init__(self) -> None:
        self.chamadas: List[Tuple[str, str, Any]] = []

    def select(self, table: str, params: Optional[Dict[str, str]] = None, fetch_all: bool = True, **_: Any) -> List[Dict[str, Any]]:
        self.chamadas.append(("select", table, params))
        return [{"id_pauta": "P-1"}, {"id_pauta": "P-2"}]

    def upsert(self, table: str, rows: List[Dict[str, Any]], on_conflict: str, **_: Any) -> int:
        self.chamadas.append(("upsert", table, rows))
        return len(rows)

    def delete(self, table: str, filters: Dict[str, str]) -> None:
        self.chamadas.append(("delete", table, filters))


class TestLoteSupabase(unittest.TestCase):
    def test_uma_consulta_um_upsert_e_um_delete(self) -> None:
        db = SupabaseDatabase.__new__(SupabaseDatabase)
        db.client = _ClienteFalso()
        self.assertEqual(db.arquivar_pautas_lote(["P-1", "P-2", "P-9"]), ["P-1", "P-2"])
        tipos = [c[0] for c in db.client.chamadas]
        self.assertEqual(tipos, ["select", "upsert"])
        self.assertEqual(db.client.chamadas[0][2]["id_pauta"], 'in.("P-1","P-2","P-9")')
        self.assertEqual([r["id_pauta"] for r in db.client.chamadas[1][2]], ["P-1", "P-2"])
        db.client.chamadas.clear()
        self.assertEqual(db.restaurar_pautas_lote(["P-1", "P-2"]), ["P-1", "P-2"])
        self.assertEqual(db.client.chamadas[-1], ("delete", "pautas_arquivadas", {"id_pauta": 'in.("P-1","P-2")'}))


class TestRotaLote(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.tmp.name) / "rota.db")
        self.db.start_execution("EXEC-R", modo="manual")
        for pid in ("P-1", "P-2"):
            self.db.insert_pauta(_pauta(pid), "EXEC-R")
        self._token = os.environ.pop(ADMIN_TOKEN_ENV, None)
        self._limite = web_server.LIMITE_TENTATIVAS_TOKEN
        web_server.LIMITE_TENTATIVAS_TOKEN = LimiteTentativas()
        case = self

        class _H(RadarAPIHandler):
            @classmethod
            def get_db(cls) -> Any:
                return case.db

            def _apply_request_pautas_arquivadas(self, db: Any) -> int:
                return 0

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.h = _H

    def tearDown(self) -> None:
        web_server.LIMITE_TENTATIVAS_TOKEN = self._limite
        os.environ.pop(ADMIN_TOKEN_ENV, None)
        if self._token is not None:
            os.environ[ADMIN_TOKEN_ENV] = self._token
        self.tmp.cleanup()

    def post(self, payload: Any, headers: Dict[str, str], ip: str = "203.0.113.9") -> Tuple[int, Dict[str, Any]]:
        h = self.h.__new__(self.h)
        h.command, h.path, h.request_version = "POST", "/api/pautas/arquivar-lote", "HTTP/1.1"
        h.requestline = "POST /api/pautas/arquivar-lote HTTP/1.1"
        h.client_address, h.close_connection, h._headers_buffer = (ip, 0), True, []
        body = json.dumps(payload).encode("utf-8")
        msg = email.message.Message()
        for k, v in headers.items():
            msg[k] = v
        msg["Content-Length"] = str(len(body))
        h.headers, h.rfile, h.wfile = msg, io.BytesIO(body), io.BytesIO()
        h.do_POST()
        head, _, resp = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.decode("latin-1").split(" ", 2)[1]), json.loads(resp.decode("utf-8") or "{}")

    def test_exige_token_e_valida_corpo(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo"
        st, _ = self.post({"ids": ["P-1"]}, JSON)
        self.assertEqual(st, 401)
        ok = dict(JSON, **{"X-Radar-Token": "segredo"})
        self.assertEqual(self.post({}, ok)[0], 400)
        self.assertEqual(self.post({"ids": "P-1"}, ok)[0], 400)
        self.assertEqual(self.post({"ids": [f"x{i}" for i in range(201)]}, ok)[0], 400)

        st, data = self.post({"ids": ["P-1", "P-2", "X"]}, ok)
        self.assertEqual(st, 200)
        self.assertEqual(data["arquivadas"], ["P-1", "P-2"])
        self.assertEqual(self.db.get_all_pautas(incluir_arquivadas=False), [])

        st, data = self.post({"ids": ["P-1", "P-2"], "restaurar": True}, ok)
        self.assertEqual((st, data["total"]), (200, 2))
        self.assertEqual(len(self.db.get_all_pautas(incluir_arquivadas=False)), 2)


if __name__ == "__main__":
    unittest.main()
