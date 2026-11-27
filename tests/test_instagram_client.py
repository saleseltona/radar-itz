"""Testes do cliente da Instagram Graph API (Business Discovery)."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from src import instagram_client
from src.instagram_client import InstagramGraphError, fetch_recent_media


def _http_error(code: int, payload: dict) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps(payload).encode("utf-8"))
    return urllib.error.HTTPError("https://graph.facebook.com", code, "Bad Request", {}, body)


class TestInstagramClient(unittest.TestCase):
    def test_versao_padrao_e_graph_facebook(self) -> None:
        self.assertTrue(instagram_client.GRAPH_BASE_URL.startswith("https://graph.facebook.com/v"))

    def test_token_do_login_do_instagram_e_recusado_sem_chamar_api(self) -> None:
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaises(InstagramGraphError) as ctx:
                fetch_recent_media("prefeituradeimperatriz", "1784", "IGAAfalso")
        urlopen.assert_not_called()
        self.assertIn("EAA", str(ctx.exception))
        self.assertNotIn("IGAAfalso", str(ctx.exception))

    def test_erro_190_inclui_orientacao_de_renovar_token(self) -> None:
        erro = _http_error(400, {"error": {"message": "Error validating access token", "code": 190}})
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            with self.assertRaises(InstagramGraphError) as ctx:
                fetch_recent_media("prefeituradeimperatriz", "1784", "EAAfalso")
        self.assertIn("token expirado", str(ctx.exception))

    def test_erro_10_indica_permissoes_faltando(self) -> None:
        erro = _http_error(403, {"error": {"message": "(#10) Application does not have permission", "code": 10}})
        with mock.patch("urllib.request.urlopen", side_effect=erro):
            with self.assertRaises(InstagramGraphError) as ctx:
                fetch_recent_media("prefeituradeimperatriz", "1784", "EAAfalso")
        self.assertIn("instagram_manage_insights", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
