"""Notificações push (Web Push) e app instalável. Sem rede: o envio usa um transporte falso."""
from __future__ import annotations

import email.message
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402

from src import web_push  # noqa: E402
from src.auto_refresh import AutoAtualizador, iniciar_agendador  # noqa: E402
from src.database import RadarDatabase  # noqa: E402
from src.pncp_sync import classificar_contratos  # noqa: E402
from src.web_server import ADMIN_TOKEN_ENV, RadarAPIHandler  # noqa: E402

APPLE = "https://web.push.apple.com/QGxyz-abc_123"


def _ua_chaves() -> Tuple[Any, str, str]:
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return priv, web_push.b64url_encode(pub), web_push.b64url_encode(os.urandom(16))


def _decifrar(corpo: bytes, ua_priv: Any, p256dh: str, auth_b64: str) -> bytes:
    """Lado do navegador (RFC 8291), para conferir a cifragem."""
    salt, rs, idlen = corpo[:16], struct.unpack("!I", corpo[16:20])[0], corpo[20]
    self_assert = rs == 4096 and idlen == 65
    as_pub = corpo[21 : 21 + idlen]
    cifrado = corpo[21 + idlen :]
    segredo = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
    h = web_push._hmac
    prk_chave = h(web_push.b64url_decode(auth_b64), segredo)
    ikm = h(prk_chave, b"WebPush: info\x00" + web_push.b64url_decode(p256dh) + as_pub + b"\x01")
    prk = h(salt, ikm)
    cek = h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = h(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    claro = AESGCM(cek).decrypt(nonce, cifrado, None)
    assert self_assert and claro.endswith(b"\x02")
    return claro[:-1]


class TestCriptografia(unittest.TestCase):
    def test_ida_e_volta(self) -> None:
        ua_priv, p256dh, auth = _ua_chaves()
        msg = json.dumps({"title": "Olá", "body": "ção"}).encode()
        corpo = web_push.criptografar(msg, p256dh, auth)
        self.assertEqual(_decifrar(corpo, ua_priv, p256dh, auth), msg)

    def test_vetor_oficial_rfc8291(self) -> None:
        """Exemplo do Apêndice A da RFC 8291: mesma entrada, mesmo corpo cifrado byte a byte."""
        efemera = ec.derive_private_key(
            int.from_bytes(web_push.b64url_decode("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"), ec.SECP256R1()
        )
        corpo = web_push.criptografar(
            b"When I grow up, I want to be a watermelon",
            "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
            "BTBZMqHH6r4Tts7J_aSIgg",
            salt=web_push.b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
            privada_efemera=efemera,
        )
        self.assertEqual(
            web_push.b64url_encode(corpo),
            "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
            "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
        )

    def test_chaves_invalidas(self) -> None:
        with self.assertRaises(ValueError):
            web_push.criptografar(b"x", web_push.b64url_encode(b"123"), web_push.b64url_encode(os.urandom(16)))

    def test_cabecalho_vapid_assinatura_valida(self) -> None:
        privada = web_push.gerar_chave_privada_vapid()
        cab = web_push.cabecalho_vapid(APPLE, privada, "https://exemplo.org", agora=1_000_000)
        self.assertTrue(cab.startswith("vapid t="))
        jwt, k = cab[len("vapid t="):].split(", k=")
        self.assertEqual(k, web_push.chave_publica_vapid(privada))
        h, c, assinatura = jwt.split(".")
        claims = json.loads(web_push.b64url_decode(c))
        self.assertEqual(claims["aud"], "https://web.push.apple.com")
        self.assertEqual(claims["sub"], "https://exemplo.org")
        self.assertEqual(claims["exp"], 1_000_000 + 12 * 3600)
        bruta = web_push.b64url_decode(assinatura)
        der = encode_dss_signature(int.from_bytes(bruta[:32], "big"), int.from_bytes(bruta[32:], "big"))
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), web_push.b64url_decode(k))
        pub.verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))  # lança se inválida

    def test_chave_privada_tamanho_errado(self) -> None:
        with self.assertRaises(web_push.PushIndisponivel):
            web_push.chave_publica_vapid(web_push.b64url_encode(b"curta"))


class TestValidacao(unittest.TestCase):
    def setUp(self) -> None:
        _, self.p256dh, self.auth = _ua_chaves()

    def insc(self, endpoint: str) -> Dict[str, Any]:
        return {"endpoint": endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}

    def test_aceita_servicos_conhecidos(self) -> None:
        for ep in (APPLE, "https://fcm.googleapis.com/fcm/send/abc", "https://updates.push.services.mozilla.com/wpush/v2/x",
                   "https://wns2-par02p.notify.windows.com/w/?token=x"):
            self.assertEqual(web_push.validar_inscricao(self.insc(ep))["endpoint"], ep)

    def test_recusa_ssrf_e_lixo(self) -> None:
        for ep in ("http://web.push.apple.com/x", "https://evil.com/x", "https://web.push.apple.com.evil.com/x",
                   "https://169.254.169.254/latest", "https://localhost/x", "https://web.push.apple.com/" + "a" * 2000):
            with self.assertRaises(ValueError, msg=ep):
                web_push.validar_inscricao(self.insc(ep))
        with self.assertRaises(ValueError):
            web_push.validar_inscricao({"endpoint": APPLE, "keys": {"p256dh": "abc", "auth": self.auth}})
        with self.assertRaises(ValueError):
            web_push.validar_inscricao(None)


class TestNotificacao(unittest.TestCase):
    def test_varredura(self) -> None:
        self.assertIsNone(web_push.montar_notificacao("varredura", {"pautas_novas": 0}))
        p = web_push.montar_notificacao("varredura", {"pautas_novas": 4, "pautas_novas_titulos": ["A", "B"]})
        self.assertEqual(p["title"], "4 pautas novas no Radar")
        self.assertIn("• A", p["body"])
        self.assertEqual(web_push.montar_notificacao("varredura", {"pautas_novas": 1})["title"], "1 pauta nova no Radar")

    def test_pncp_so_no_incremental(self) -> None:
        amostra = [{"fornecedor_nome": "Construtora X", "valor_global": 1234567.8}]
        self.assertIsNone(web_push.montar_notificacao("pncp", {"inseridos": 900, "modo": "carga_completa", "novos_amostra": amostra}))
        p = web_push.montar_notificacao("pncp", {"inseridos": 2, "modo": "incremental", "novos_amostra": amostra})
        self.assertEqual(p["title"], "2 contratos novos no PNCP")
        self.assertIn("Construtora X — R$ 1.234.567,80", p["body"])

    def test_erro_e_outras_tarefas_nao_avisam(self) -> None:
        self.assertIsNone(web_push.montar_notificacao("varredura", {"pautas_novas": 3, "erro": "x"}))
        self.assertIsNone(web_push.montar_notificacao("fiorilli", {"total_registros": 10}))


class TestEnvioSqlite(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.tmp.name) / "push.db")
        self._antes = os.environ.get(web_push.VAPID_PRIVATE_ENV)
        os.environ[web_push.VAPID_PRIVATE_ENV] = web_push.gerar_chave_privada_vapid()
        self.ua_priv, self.p256dh, self.auth = _ua_chaves()

    def tearDown(self) -> None:
        if self._antes is None:
            os.environ.pop(web_push.VAPID_PRIVATE_ENV, None)
        else:
            os.environ[web_push.VAPID_PRIVATE_ENV] = self._antes
        self.tmp.cleanup()

    def salvar(self, endpoint: str) -> None:
        web_push.salvar_inscricao(self.db, {"endpoint": endpoint, "p256dh": self.p256dh, "auth": self.auth}, "iPhone")

    def test_envia_e_remove_expiradas(self) -> None:
        self.salvar(APPLE)
        self.salvar("https://fcm.googleapis.com/fcm/send/velha")
        self.salvar(APPLE)  # mesma inscrição não duplica
        enviados: List[Tuple[str, Dict[str, str], bytes]] = []

        def falso(metodo: str, url: str, headers: Dict[str, str], corpo: bytes, timeout: int) -> Tuple[int, bytes]:
            enviados.append((url, headers, corpo))
            return (201, b"") if "apple" in url else (410, b"")

        tot = web_push.enviar_para_todos(self.db, {"title": "T", "body": "B"}, enviar=falso)
        self.assertEqual(tot, {"enviadas": 1, "falhas": 0, "removidas": 1, "aparelhos": 2})
        self.assertEqual([i["endpoint"] for i in web_push.listar_inscricoes(self.db)], [APPLE])
        url, headers, corpo = next(e for e in enviados if "apple" in e[0])
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertIn("vapid t=", headers["Authorization"])
        self.assertEqual(json.loads(_decifrar(corpo, self.ua_priv, self.p256dh, self.auth)), {"title": "T", "body": "B"})

    def test_sem_chave_nao_envia(self) -> None:
        os.environ.pop(web_push.VAPID_PRIVATE_ENV, None)
        self.salvar(APPLE)
        tot = web_push.enviar_para_todos(self.db, {"title": "T"}, enviar=lambda *a: self.fail("não deveria enviar"))
        self.assertEqual(tot["enviadas"], 0)

    def test_limite_de_aparelhos(self) -> None:
        for i in range(web_push.MAX_INSCRICOES):
            self.salvar(f"https://fcm.googleapis.com/fcm/send/{i}")
        with self.assertRaises(ValueError):
            self.salvar("https://fcm.googleapis.com/fcm/send/extra")
        self.salvar("https://fcm.googleapis.com/fcm/send/0")  # atualizar existente continua valendo

    def test_remover(self) -> None:
        self.salvar(APPLE)
        web_push.remover_inscricao(self.db, APPLE)
        self.assertEqual(web_push.listar_inscricoes(self.db), [])


class TestGanchosColeta(unittest.TestCase):
    def test_ao_concluir_so_com_sucesso(self) -> None:
        avisos: List[Tuple[str, Dict[str, Any]]] = []
        auto = AutoAtualizador(
            {"varredura": lambda ctx: {"pautas_novas": 2}, "pncp": lambda ctx: {"erro": "PNCP fora"}},
            {"varredura": 60, "pncp": 60, "fiorilli": 60},
            iniciar=lambda alvo: alvo(),
            ao_concluir=lambda nome, res: avisos.append((nome, res)),
        )
        auto.disparar({})
        self.assertEqual(avisos, [("varredura", {"pautas_novas": 2})])
        auto.executar_manual("varredura", lambda: {"pautas_novas": 1})
        self.assertEqual(len(avisos), 2)

    def test_falha_no_aviso_nao_quebra_tarefa(self) -> None:
        def explode(nome: str, res: Dict[str, Any]) -> None:
            raise RuntimeError("push caiu")

        auto = AutoAtualizador({"varredura": lambda ctx: {"pautas_novas": 2}}, {"varredura": 60}, iniciar=lambda a: a(), ao_concluir=explode)
        auto.disparar({})
        self.assertTrue(auto.status()["tarefas"]["varredura"]["ok"])

    def test_agendador_dispara(self) -> None:
        disparos: List[int] = []

        class Falso:
            def disparar(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
                disparos.append(1)
                return {}

        t = iniciar_agendador(lambda: Falso(), dormir=lambda s: None, rodadas=3)
        t.join(2)
        self.assertEqual(len(disparos), 3)

    def test_amostra_de_contratos_novos(self) -> None:
        contratos = [
            {"numero_controle_pncp": f"C{i}", "fornecedor_nome": f"F{i}", "valor_global": 10.0 * i, "objeto": "Obra"}
            for i in range(8)
        ]
        res = classificar_contratos(contratos, {"C0": dict(contratos[0])}, {})
        self.assertEqual(res["inseridos"], 7)
        self.assertEqual([c["numero_controle_pncp"] for c in res["novos_amostra"]], ["C1", "C2", "C3", "C4", "C5"])


class TestRotasPush(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = RadarDatabase(Path(self.tmp.name) / "rotas.db")
        self._env = {k: os.environ.get(k) for k in (ADMIN_TOKEN_ENV, web_push.VAPID_PRIVATE_ENV)}
        os.environ[ADMIN_TOKEN_ENV] = "segredo-teste"
        os.environ[web_push.VAPID_PRIVATE_ENV] = web_push.gerar_chave_privada_vapid()
        _, self.p256dh, self.auth = _ua_chaves()
        caso = self

        class _Handler(RadarAPIHandler):
            @classmethod
            def get_db(cls) -> Any:
                return caso.db

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

        self.handler_cls = _Handler

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def call(self, method: str, path: str, headers: Optional[Dict[str, str]] = None, payload: Any = None) -> Tuple[int, Dict[str, str], bytes]:
        h = self.handler_cls.__new__(self.handler_cls)
        h.command, h.path, h.request_version = method, path, "HTTP/1.1"
        h.requestline = f"{method} {path} HTTP/1.1"
        h.client_address = ("203.0.113.9", 0)
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
        linhas = head.decode("latin-1").split("\r\n")
        cabecalhos = {ln.split(":", 1)[0].lower(): ln.split(":", 1)[1].strip() for ln in linhas[1:] if ":" in ln}
        return int(linhas[0].split(" ", 2)[1]), cabecalhos, resp

    def test_arquivos_do_app(self) -> None:
        status, cab, corpo = self.call("GET", "/manifest.webmanifest")
        self.assertEqual(status, 200)
        manifesto = json.loads(corpo)
        self.assertEqual(manifesto["display"], "standalone")
        status, cab, corpo = self.call("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", cab["content-type"])
        self.assertIn(b"showNotification", corpo)
        status, cab, corpo = self.call("GET", "/apple-touch-icon.png")
        self.assertEqual((status, corpo[:4]), (200, b"\x89PNG"))
        for icone in manifesto["icons"]:
            self.assertEqual(self.call("GET", icone["src"])[0], 200, icone["src"])

    def test_chave_publica(self) -> None:
        status, _, corpo = self.call("GET", "/api/push/chave-publica")
        dados = json.loads(corpo)
        self.assertEqual(status, 200)
        self.assertTrue(dados["configurado"])
        self.assertEqual(len(web_push.b64url_decode(dados["chave_publica"])), 65)
        self.assertNotIn(os.environ[web_push.VAPID_PRIVATE_ENV], corpo.decode())
        os.environ.pop(web_push.VAPID_PRIVATE_ENV)
        self.assertFalse(json.loads(self.call("GET", "/api/push/chave-publica")[2])["configurado"])

    def test_inscrever_exige_token_e_valida(self) -> None:
        insc = {"endpoint": APPLE, "keys": {"p256dh": self.p256dh, "auth": self.auth}}
        sem = self.call("POST", "/api/push/inscrever", {"Content-Type": "application/json"}, {"inscricao": insc})
        self.assertEqual(sem[0], 401)
        self.assertEqual(web_push.listar_inscricoes(self.db), [])
        cab = {"Content-Type": "application/json", "X-Radar-Token": "segredo-teste"}
        ruim = dict(insc, endpoint="https://evil.com/x")
        self.assertEqual(self.call("POST", "/api/push/inscrever", cab, {"inscricao": ruim})[0], 400)
        self.assertEqual(self.call("POST", "/api/push/inscrever", cab, {"inscricao": insc})[0], 200)
        self.assertEqual(len(web_push.listar_inscricoes(self.db)), 1)
        self.assertEqual(self.call("POST", "/api/push/cancelar", cab, {"endpoint": APPLE})[0], 200)
        self.assertEqual(web_push.listar_inscricoes(self.db), [])

    def test_sem_chave_vapid_responde_503(self) -> None:
        os.environ.pop(web_push.VAPID_PRIVATE_ENV)
        cab = {"Content-Type": "application/json", "X-Radar-Token": "segredo-teste"}
        self.assertEqual(self.call("POST", "/api/push/testar", cab, {})[0], 503)

    def test_dashboard_tem_manifesto_e_script(self) -> None:
        html = (Path(__file__).resolve().parent.parent / "static" / "stitch_dashboard.html").read_text(encoding="utf-8")
        self.assertIn('rel="manifest"', html)
        self.assertIn("apple-touch-icon", html)
        self.assertIn("/static/radar_push.js", html)



class TestWebPushEndurecimento(unittest.TestCase):
    def test_envio_recusa_host_fora_da_lista_e_nao_segue_redirect(self) -> None:
        from src import web_push as wp
        status, _ = wp._enviar_urllib("POST", "https://exemplo.invalid/x", {}, b"", 1)
        self.assertEqual(status, 0)
        self.assertIsNone(wp._SemRedirecionar().redirect_request(None, None, 302, "", {}, "http://127.0.0.1/"))

    def test_chave_vapid_mascarada(self) -> None:
        import os
        from src.config import mask_secrets
        antes = os.environ.get("RADAR_VAPID_PRIVATE_KEY")
        os.environ["RADAR_VAPID_PRIVATE_KEY"] = "chave-privada-de-teste"
        try:
            self.assertNotIn("chave-privada-de-teste", mask_secrets("erro chave-privada-de-teste"))
        finally:
            if antes is None:
                os.environ.pop("RADAR_VAPID_PRIVATE_KEY", None)
            else:
                os.environ["RADAR_VAPID_PRIVATE_KEY"] = antes


if __name__ == "__main__":
    unittest.main()
