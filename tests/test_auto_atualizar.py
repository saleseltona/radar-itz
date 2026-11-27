"""
Testes da atualização automática ao abrir o painel (src/auto_refresh.py e /api/auto-atualizar).
Herméticos: relógio falso, executores falsos, sem rede e sem portas.
"""
from __future__ import annotations

import email.message
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.auto_refresh import AutoAtualizador, TarefaOcupada
from src.database import RadarDatabase
from src.pncp_sync import SyncEmAndamento
from src.web_server import ADMIN_TOKEN_ENV, RadarAPIHandler, auto_pncp, pncp_carga_ativa_em_outra_sessao


class Relogio:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def _executores(chamadas: List[str], resultados: Optional[Dict[str, Any]] = None) -> Dict[str, Callable]:
    resultados = resultados or {}

    def fazer(nome: str) -> Callable:
        def run(ctx: Dict[str, Any]) -> Dict[str, Any]:
            chamadas.append(nome)
            r = resultados.get(nome, {"ok": True})
            if isinstance(r, Exception):
                raise r
            return r
        return run

    return {n: fazer(n) for n in ("varredura", "pncp", "fiorilli")}


INTERVALOS = {"varredura": 1800, "pncp": 1800, "fiorilli": 10800}


class TestAutoAtualizador(unittest.TestCase):
    def setUp(self) -> None:
        self.relogio = Relogio()
        self.chamadas: List[str] = []
        self.pendentes: List[Callable[[], None]] = []

    def novo(self, resultados: Optional[Dict[str, Any]] = None, imediato: bool = True) -> AutoAtualizador:
        iniciar = (lambda alvo: alvo()) if imediato else self.pendentes.append
        return AutoAtualizador(_executores(self.chamadas, resultados), INTERVALOS, self.relogio, iniciar)

    def test_primeira_abertura_roda_tudo_em_ordem(self) -> None:
        auto = self.novo()
        st = auto.disparar()
        self.assertEqual(st["iniciadas"], ["varredura", "pncp", "fiorilli"])
        self.assertEqual(self.chamadas, ["varredura", "pncp", "fiorilli"])
        self.assertFalse(st["em_andamento"])
        self.assertTrue(all(t["situacao"] == "ok" for t in st["tarefas"].values()))

    def test_f5_seguido_nao_repete_dentro_do_intervalo(self) -> None:
        auto = self.novo()
        auto.disparar()
        self.relogio.t += 60
        st = auto.disparar()
        self.assertEqual(st["iniciadas"], [])
        self.assertEqual(len(self.chamadas), 3)
        self.assertGreater(st["tarefas"]["pncp"]["proxima_em_segundos"], 0)

    def test_so_roda_as_vencidas(self) -> None:
        auto = self.novo()
        auto.disparar()
        self.relogio.t += 1801  # varredura e PNCP venceram; Fiorilli (3 h) ainda não
        st = auto.disparar()
        self.assertEqual(st["iniciadas"], ["varredura", "pncp"])
        self.assertEqual(self.chamadas[3:], ["varredura", "pncp"])

    def test_forcar_roda_tudo(self) -> None:
        auto = self.novo()
        auto.disparar()
        self.assertEqual(auto.disparar(forcar=True)["iniciadas"], ["varredura", "pncp", "fiorilli"])

    def test_nao_empilha_rodadas_enquanto_em_andamento(self) -> None:
        auto = self.novo(imediato=False)
        st1 = auto.disparar()
        self.assertTrue(st1["em_andamento"])
        self.assertEqual(st1["tarefas"]["varredura"]["situacao"], "na_fila")
        st2 = auto.disparar(forcar=True)
        self.assertEqual(st2["iniciadas"], [])
        self.assertEqual(len(self.pendentes), 1)
        self.pendentes[0]()
        self.assertEqual(self.chamadas, ["varredura", "pncp", "fiorilli"])
        self.assertFalse(auto.status()["em_andamento"])

    def test_erro_de_uma_tarefa_nao_para_as_outras(self) -> None:
        auto = self.novo({"pncp": RuntimeError("falhou token=abc123segredo"), "fiorilli": {"erro": "Portal fora"}})
        st = auto.disparar()
        self.assertEqual(self.chamadas, ["varredura", "pncp", "fiorilli"])
        self.assertEqual(st["tarefas"]["varredura"]["situacao"], "ok")
        self.assertEqual(st["tarefas"]["pncp"]["situacao"], "erro")
        self.assertEqual(st["tarefas"]["fiorilli"]["erro"], "Portal fora")
        # Falha também conta como tentativa: não martela a fonte a cada F5
        self.relogio.t += 60
        self.assertEqual(auto.disparar()["iniciadas"], [])

    def test_manual_e_automatico_nao_rodam_juntos(self) -> None:
        auto = self.novo()
        bloqueio: Dict[str, Any] = {}

        def manual_que_tenta_automatico() -> Dict[str, Any]:
            # Enquanto o botão roda, a automática da mesma tarefa é pulada
            bloqueio["st"] = auto.disparar()
            return {"pautas_novas": 2}

        res = auto.executar_manual("varredura", manual_que_tenta_automatico)
        self.assertEqual(res["pautas_novas"], 2)
        self.assertNotIn("varredura", self.chamadas)
        self.assertEqual(self.chamadas, ["pncp", "fiorilli"])

    def test_manual_recusado_quando_automatico_rodando(self) -> None:
        auto = self.novo()
        trava = auto._travas["fiorilli"]
        trava.acquire()
        try:
            self.assertTrue(auto.tarefa_rodando("fiorilli"))
            with self.assertRaises(TarefaOcupada):
                auto.executar_manual("fiorilli", lambda: {})
        finally:
            trava.release()

    def test_manual_conta_como_atualizacao_recente(self) -> None:
        auto = self.novo()
        auto.executar_manual("varredura", lambda: {"pautas_novas": 1})
        st = auto.disparar()
        self.assertEqual(st["iniciadas"], ["pncp", "fiorilli"])
        self.assertEqual(st["tarefas"]["varredura"]["resultado"], {"pautas_novas": 1})


class FakeDb:
    def __init__(self, estado: Optional[Dict[str, Any]] = None) -> None:
        self.estado = estado or {}

    def get_pncp_sync_state(self) -> Dict[str, Any]:
        return self.estado


class TestAutoPncp(unittest.TestCase):
    def test_respeita_carga_conduzida_por_outra_sessao(self) -> None:
        agora = datetime.now()
        estado = {"cursor_pendente": {"modo": "carga"}, "ultima_execucao": (agora - timedelta(seconds=30)).isoformat()}
        self.assertTrue(pncp_carga_ativa_em_outra_sessao(estado, agora))
        chamadas: List[int] = []
        res = auto_pncp(FakeDb(estado), lote=lambda *a, **k: chamadas.append(1) or {})
        self.assertTrue(res["pulado"])
        self.assertEqual(chamadas, [])

    def test_retoma_carga_abandonada(self) -> None:
        agora = datetime.now()
        estado = {"cursor_pendente": {"modo": "carga"}, "ultima_execucao": (agora - timedelta(hours=2)).isoformat()}
        self.assertFalse(pncp_carga_ativa_em_outra_sessao(estado, agora))
        self.assertFalse(pncp_carga_ativa_em_outra_sessao({"ultima_execucao": agora.isoformat()}, agora))

    def test_roda_lotes_ate_concluir(self) -> None:
        respostas = [
            {"inseridos": 3, "atualizados": 1, "parcial": True, "erros": []},
            {"inseridos": 2, "atualizados": 0, "parcial": False, "erros": [], "ultima_data_sincronizada": "2026-11-27"},
        ]
        modos: List[str] = []

        def lote(db: Any, normalizar: Any, modo: str = "") -> Dict[str, Any]:
            modos.append(modo)
            return respostas.pop(0)

        res = auto_pncp(FakeDb(), lote=lote)
        self.assertEqual((res["inseridos"], res["atualizados"], res["lotes"]), (5, 1, 2))
        self.assertFalse(res["parcial"])
        self.assertEqual(modos, ["auto", "auto"])

    def test_limite_de_lotes_por_rodada(self) -> None:
        res = auto_pncp(FakeDb(), lote=lambda *a, **k: {"inseridos": 1, "parcial": True, "erros": []}, max_lotes=4)
        self.assertEqual(res["lotes"], 4)
        self.assertTrue(res["parcial"])

    def test_lock_ocupado_vira_pulado(self) -> None:
        def lote(*a: Any, **k: Any) -> Dict[str, Any]:
            raise SyncEmAndamento("ocupado")

        res = auto_pncp(FakeDb(), lote=lote)
        self.assertTrue(res["pulado"])
        self.assertNotIn("erro", res)

    def test_pncp_fora_do_ar_sem_progresso_vira_erro(self) -> None:
        res = auto_pncp(FakeDb(), lote=lambda *a, **k: {"parcial": True, "erros": ["PMI: timeout"], "janelas_concluidas": 0})
        self.assertIn("erro", res)


class TestRotaAutoAtualizar(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.temp_dir.name) / "auto.db")
        self._token_antes = os.environ.pop(ADMIN_TOKEN_ENV, None)
        self.chamadas: List[str] = []
        self.auto = AutoAtualizador(_executores(self.chamadas), INTERVALOS, Relogio(), lambda alvo: alvo())
        test_case = self

        class _Handler(RadarAPIHandler):
            @classmethod
            def get_db(cls) -> Any:
                return test_case.db

            def auto_atualizador(self) -> AutoAtualizador:
                return test_case.auto

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.handler_cls = _Handler

    def tearDown(self) -> None:
        os.environ.pop(ADMIN_TOKEN_ENV, None)
        os.environ.pop("RADAR_AUTO_ATUALIZAR", None)
        if self._token_antes is not None:
            os.environ[ADMIN_TOKEN_ENV] = self._token_antes
        self.temp_dir.cleanup()

    def call(self, method: str, path: str, headers: Optional[Dict[str, str]] = None, payload: Any = None) -> Tuple[int, Dict[str, Any]]:
        h = self.handler_cls.__new__(self.handler_cls)
        h.command, h.path, h.request_version = method, path, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.client_address = ("203.0.113.7", 0)
        h.close_connection = True
        h._headers_buffer = []
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        msg = email.message.Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        if body:
            msg["Content-Length"] = str(len(body))
        h.headers, h.rfile, h.wfile = msg, io.BytesIO(body), io.BytesIO()
        getattr(h, f"do_{method}")()
        head, _, resp = h.wfile.getvalue().partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), json.loads(resp or b"{}")

    def post(self, path: str, token: str = "", payload: Any = None) -> Tuple[int, Dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Radar-Token"] = token
        return self.call("POST", path, headers, payload if payload is not None else {})

    def test_post_exige_token(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-teste"
        status, _ = self.post("/api/auto-atualizar")
        self.assertEqual(status, 401)
        self.assertEqual(self.chamadas, [])

    def test_post_com_token_dispara_e_status_e_publico(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-teste"
        status, data = self.post("/api/auto-atualizar", "segredo-teste", {"consultas_adicionais": ["enchente"]})
        self.assertEqual(status, 200)
        self.assertEqual(data["iniciadas"], ["varredura", "pncp", "fiorilli"])
        status, st = self.call("GET", "/api/auto-atualizar")
        self.assertEqual(status, 200)
        self.assertTrue(st["habilitada"])
        self.assertEqual(st["tarefas"]["pncp"]["situacao"], "ok")

    def test_desligada_por_ambiente(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-teste"
        os.environ["RADAR_AUTO_ATUALIZAR"] = "0"
        status, data = self.post("/api/auto-atualizar", "segredo-teste")
        self.assertEqual(status, 200)
        self.assertTrue(data["desabilitada"])
        self.assertEqual(self.chamadas, [])

    def test_botao_pncp_espera_automatico(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-teste"
        trava = self.auto._travas["pncp"]
        trava.acquire()
        try:
            status, data = self.post("/api/pncp/sync", "segredo-teste")
        finally:
            trava.release()
        self.assertEqual(status, 409)
        self.assertTrue(data["em_andamento"])

    def test_botao_varredura_espera_automatico(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-teste"
        trava = self.auto._travas["varredura"]
        trava.acquire()
        try:
            status, data = self.post("/api/executar", "segredo-teste")
        finally:
            trava.release()
        self.assertEqual(status, 409)


class TestFrontend(unittest.TestCase):
    def test_painel_carrega_script_e_nao_abre_prompt_automatico(self) -> None:
        base = Path(__file__).resolve().parent.parent / "static"
        html = (base / "stitch_dashboard.html").read_text(encoding="utf-8")
        self.assertIn('/static/radar_auto.js', html)
        auto_js = (base / "radar_auto.js").read_text(encoding="utf-8")
        self.assertIn("radarSemPrompt: true", auto_js)
        auth_js = (base / "radar_auth.js").read_text(encoding="utf-8")
        self.assertIn("options.radarSemPrompt", auth_js)
        self.assertIn("radarHasAdminToken", auth_js)


if __name__ == "__main__":
    unittest.main()
