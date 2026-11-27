"""
Testes de segurança do servidor web: arquivos estáticos e autorização das rotas que gravam dados.
Herméticos: usam banco temporário, não abrem portas e não acessam a rede.
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

from src.database import RadarDatabase
import src.web_server as web_server
from src.web_server import ADMIN_TOKEN_ENV, LimiteTentativas, RadarAPIHandler, resolve_static_file

JSON_HEADERS = {"Content-Type": "application/json"}


class TestSegurancaWeb(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.temp_dir.name) / "seguranca.db")
        self._token_antes = os.environ.pop(ADMIN_TOKEN_ENV, None)
        self.reidratacoes: List[str] = []
        self._limite_antes = web_server.LIMITE_TENTATIVAS_TOKEN
        web_server.LIMITE_TENTATIVAS_TOKEN = LimiteTentativas()
        self._render_antes = os.environ.pop("RENDER", None)

        test_case = self

        class _Handler(RadarAPIHandler):
            @classmethod
            def get_db(cls) -> Any:
                return test_case.db

            def _apply_request_pautas_arquivadas(self, db: Any) -> int:
                test_case.reidratacoes.append("pautas")
                return 0

            def _apply_request_pncp_retificacoes(self, db: Any) -> int:
                test_case.reidratacoes.append("pncp")
                return 0

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.handler_cls = _Handler

    def tearDown(self) -> None:
        web_server.LIMITE_TENTATIVAS_TOKEN = self._limite_antes
        os.environ.pop("RENDER", None)
        if self._render_antes is not None:
            os.environ["RENDER"] = self._render_antes
        os.environ.pop(ADMIN_TOKEN_ENV, None)
        if self._token_antes is not None:
            os.environ[ADMIN_TOKEN_ENV] = self._token_antes
        self.temp_dir.cleanup()

    def call(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        payload: Any = None,
        client_ip: str = "127.0.0.1",
    ) -> Tuple[int, Dict[str, str], bytes]:
        h = self.handler_cls.__new__(self.handler_cls)
        h.command = method
        h.path = path
        h.request_version = "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.client_address = (client_ip, 0)
        h.close_connection = True
        h._headers_buffer = []
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        msg = email.message.Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        if body:
            msg["Content-Length"] = str(len(body))
        h.headers = msg
        h.rfile = io.BytesIO(body)
        h.wfile = io.BytesIO()
        getattr(h, f"do_{method}")()
        head, _, resp_body = h.wfile.getvalue().partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        resp_headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                resp_headers[k.strip().lower()] = v.strip()
        return int(lines[0].split(" ", 2)[1]), resp_headers, resp_body

    # --- Arquivos estáticos -------------------------------------------------

    def test_static_serve_arquivo_legitimo(self) -> None:
        status, _, body = self.call("GET", "/static/radar_auth.js")
        self.assertEqual(status, 200)
        self.assertIn(b"X-Radar-Token", body)

    def test_static_bloqueia_saida_da_pasta(self) -> None:
        tentativas = [
            "/static/../README.md",
            "/static/../src/config.py",
            "/static//etc/passwd",
            "/static/../../../../etc/passwd",
            "/static/%2e%2e/README.md",
            "/static/%2e%2e%2fREADME.md",
            "/static/..%2f..%2fREADME.md",
            "/api/index?__route=/static/../README.md",
        ]
        for caminho in tentativas:
            with self.subTest(caminho=caminho):
                status, _, _ = self.call("GET", caminho)
                self.assertEqual(status, 404)

    def test_parametro_route_antigo_e_ignorado(self) -> None:
        # O rewrite `?__route=` era só da Vercel; agora a query não muda a rota.
        status, _, body = self.call("GET", "/?__route=/static/../README.md")
        self.assertEqual(status, 200)
        self.assertNotIn(b"Radar de Pautas de Imperatriz (MA)\n", body[:200])
        self.assertIn(b"<html", body[:2000].lower())

    def test_resolve_static_file_recusa_caminhos_externos(self) -> None:
        self.assertIsNone(resolve_static_file("../README.md"))
        self.assertIsNone(resolve_static_file("/etc/passwd"))
        self.assertIsNone(resolve_static_file("nao_existe.js"))
        self.assertIsNone(resolve_static_file("arquivo\x00.js"))
        self.assertIsNotNone(resolve_static_file("radar_auth.js"))

    # --- Rotas de escrita ---------------------------------------------------

    def test_post_exige_json(self) -> None:
        status, _, _ = self.call("POST", "/api/pautas/arquivar", {"Content-Type": "text/plain"}, {"id_pauta": "X"})
        self.assertEqual(status, 415)
        status, _, _ = self.call("POST", "/api/executar")
        self.assertEqual(status, 415)

    def test_sem_token_aceita_somente_acesso_local_direto(self) -> None:
        # Local direto: passa da autorização e cai na validação do corpo (400).
        status, _, _ = self.call("POST", "/api/pautas/arquivar", JSON_HEADERS, {})
        self.assertEqual(status, 400)

        bloqueios = [
            ({}, "203.0.113.7"),
            ({"X-Forwarded-For": "203.0.113.7"}, "127.0.0.1"),
            ({"Forwarded": "for=203.0.113.7"}, "127.0.0.1"),
            ({"Host": "radar.example.com"}, "127.0.0.1"),
        ]
        for extra, ip in bloqueios:
            with self.subTest(extra=extra, ip=ip):
                headers = dict(JSON_HEADERS, **extra)
                status, _, body = self.call("POST", "/api/pautas/arquivar", headers, {"id_pauta": "X"}, client_ip=ip)
                self.assertEqual(status, 403)
                self.assertIn("RADAR_ADMIN_TOKEN", body.decode("utf-8"))

        status, _, _ = self.call("POST", "/api/pautas/arquivar", dict(JSON_HEADERS, Host="localhost:8000"), {})
        self.assertEqual(status, 400)

    def test_com_token_exige_token_correto(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        for headers in (
            JSON_HEADERS,
            dict(JSON_HEADERS, **{"X-Radar-Token": "errado"}),
            dict(JSON_HEADERS, Authorization="Bearer errado"),
        ):
            with self.subTest(headers=headers):
                status, _, _ = self.call("POST", "/api/pautas/arquivar", headers, {"id_pauta": "X"})
                self.assertEqual(status, 401)

        for headers in (
            dict(JSON_HEADERS, **{"X-Radar-Token": "segredo-de-teste"}),
            dict(JSON_HEADERS, Authorization="Bearer segredo-de-teste"),
        ):
            with self.subTest(headers=headers):
                status, _, _ = self.call(
                    "POST", "/api/pautas/arquivar", dict(headers, **{"X-Forwarded-For": "203.0.113.7"}), {},
                    client_ip="10.0.0.5",
                )
                self.assertEqual(status, 400)

    def test_get_so_reidrata_estado_quando_autorizado(self) -> None:
        self.call("GET", "/api/stats", {"X-Forwarded-For": "203.0.113.7"})
        self.assertEqual(self.reidratacoes, [])

        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        self.call("GET", "/api/stats", {"X-Radar-Token": "errado"})
        self.assertEqual(self.reidratacoes, [])

        self.call("GET", "/api/stats", {"X-Radar-Token": "segredo-de-teste"})
        self.assertEqual(sorted(self.reidratacoes), ["pautas", "pncp"])

    def test_options_nao_libera_outras_origens(self) -> None:
        status, headers, _ = self.call("OPTIONS", "/api/pautas/arquivar")
        self.assertEqual(status, 204)
        self.assertNotIn("access-control-allow-origin", headers)


    # --- Força bruta no token -----------------------------------------------

    def _post_token(self, token: str, ip: str = "198.51.100.9", extra: Optional[Dict[str, str]] = None) -> int:
        headers = dict(JSON_HEADERS, **{"X-Radar-Token": token}, **(extra or {}))
        status, _, _ = self.call("POST", "/api/pautas/arquivar", headers, {}, client_ip=ip)
        return status

    def test_bloqueia_ip_apos_5_tokens_errados_por_minuto(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        for i in range(5):
            self.assertEqual(self._post_token(f"palpite-{i}"), 401)
        headers = dict(JSON_HEADERS, **{"X-Radar-Token": "palpite-6"})
        status, resp_headers, body = self.call("POST", "/api/pautas/arquivar", headers, {}, client_ip="198.51.100.9")
        self.assertEqual(status, 429)
        self.assertTrue(1 <= int(resp_headers["retry-after"]) <= 60)
        self.assertIn("tentativas", body.decode("utf-8"))
        # Durante o bloqueio nem o token certo passa daquele IP; outro IP segue normal.
        self.assertEqual(self._post_token("segredo-de-teste"), 429)
        self.assertEqual(self._post_token("segredo-de-teste", ip="198.51.100.10"), 400)
        # GET com token também não reidrata estado durante o bloqueio.
        self.call("GET", "/api/stats", {"X-Radar-Token": "segredo-de-teste"}, client_ip="198.51.100.9")
        self.assertEqual(self.reidratacoes, [])

    def test_bloqueio_expira_depois_da_janela(self) -> None:
        agora = [1000.0]
        web_server.LIMITE_TENTATIVAS_TOKEN = LimiteTentativas(relogio=lambda: agora[0])
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        for i in range(5):
            self._post_token(f"palpite-{i}")
        self.assertEqual(self._post_token("segredo-de-teste"), 429)
        agora[0] += 61
        self.assertEqual(self._post_token("segredo-de-teste"), 400)

    def test_mesmo_token_errado_repetido_conta_uma_vez(self) -> None:
        # Navegador com token antigo salvo não bloqueia o próprio dono.
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        for _ in range(20):
            self.assertEqual(self._post_token("token-antigo"), 401)
        self.assertEqual(self._post_token("segredo-de-teste"), 400)

    def test_sem_token_nao_conta_como_tentativa(self) -> None:
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        for _ in range(10):
            status, _, _ = self.call("POST", "/api/pautas/arquivar", JSON_HEADERS, {}, client_ip="198.51.100.9")
            self.assertEqual(status, 401)
        self.assertEqual(self._post_token("segredo-de-teste"), 400)

    def test_no_render_usa_ip_anotado_pelo_proxy(self) -> None:
        # No Render todo mundo chega pelo mesmo proxy; o IP real é o último do X-Forwarded-For,
        # e o cliente não escapa do limite inventando IPs à esquerda.
        os.environ["RENDER"] = "true"
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste"
        for i in range(5):
            falso = {"X-Forwarded-For": f"10.9.9.{i}, 203.0.113.50"}
            self.assertEqual(self._post_token(f"palpite-{i}", ip="10.0.0.1", extra=falso), 401)
        self.assertEqual(
            self._post_token("segredo-de-teste", ip="10.0.0.1", extra={"X-Forwarded-For": "203.0.113.50"}), 429
        )
        self.assertEqual(
            self._post_token("segredo-de-teste", ip="10.0.0.1", extra={"X-Forwarded-For": "203.0.113.51"}), 400
        )

    def test_limite_nao_cresce_sem_fim(self) -> None:
        limite = LimiteTentativas(max_ips=50)
        for i in range(500):
            limite.registrar_falha(f"ip-{i}", "x")
        self.assertLessEqual(len(limite._falhas), 50)

    def test_token_comparado_em_tempo_constante(self) -> None:
        import inspect
        fonte = inspect.getsource(RadarAPIHandler._is_write_authorized)
        self.assertIn("hmac.compare_digest", fonte)
        self.assertNotIn("sent == expected", fonte)

    # --- Corpo, cabeçalhos e páginas ----------------------------------------

    def test_post_recusa_corpo_grande(self) -> None:
        headers = dict(JSON_HEADERS, **{"Content-Length": str(web_server.MAX_CORPO_POST + 1)})
        status, _, _ = self.call("POST", "/api/pautas/arquivar", headers)
        self.assertEqual(status, 413)
        status, _, _ = self.call("POST", "/api/pautas/arquivar", dict(JSON_HEADERS, **{"Content-Length": "-5"}))
        self.assertEqual(status, 413)

    def test_respostas_tem_nosniff(self) -> None:
        _, headers, _ = self.call("GET", "/static/radar_auth.js")
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")
        _, headers, _ = self.call("GET", "/health")
        self.assertEqual(headers.get("x-content-type-options"), "nosniff")

    def test_clipping_escapa_html_das_fontes(self) -> None:
        original = web_server.OUTPUT_DIR
        web_server.OUTPUT_DIR = Path(self.temp_dir.name)
        try:
            (web_server.OUTPUT_DIR / "clipping_do_dia.md").write_text(
                "# Pauta <script>alert(1)</script> & <img src=x onerror=alert(2)>", encoding="utf-8"
            )
            status, _, body = self.call("GET", "/clipping")
        finally:
            web_server.OUTPUT_DIR = original
        self.assertEqual(status, 200)
        texto = body.decode("utf-8")
        self.assertNotIn("<script>alert(1)", texto)
        self.assertNotIn("<img src=x", texto)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", texto)

    def test_token_admin_e_mascarado_em_erros(self) -> None:
        from src.config import mask_secrets
        os.environ[ADMIN_TOKEN_ENV] = "segredo-de-teste-123"
        self.assertNotIn("segredo-de-teste-123", mask_secrets("falhou com segredo-de-teste-123"))


if __name__ == "__main__":
    unittest.main()
