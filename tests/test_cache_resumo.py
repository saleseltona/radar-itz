"""
Cache curto do /api/transparencia/resumo (src/web_server.py): prazo, invalidação por gravações
e por tarefas de sincronização. Hermético: relógio falso, sem rede e sem portas.
"""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from src import web_server
from src.web_server import CacheResumoTransparencia, RadarAPIHandler


class Relogio:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class TestCacheResumo(unittest.TestCase):
    def setUp(self) -> None:
        self.relogio = Relogio()
        self.cache = CacheResumoTransparencia(relogio=self.relogio)
        self.chamadas = 0

    def calc(self):
        self.chamadas += 1
        return {"n": self.chamadas}

    def test_guarda_ate_o_prazo(self) -> None:
        self.assertEqual(self.cache.obter("k", self.calc, 300), ({"n": 1}, False))
        self.relogio.t += 299
        self.assertEqual(self.cache.obter("k", self.calc, 300), ({"n": 1}, True))
        self.relogio.t += 2
        self.assertEqual(self.cache.obter("k", self.calc, 300), ({"n": 2}, False))

    def test_chaves_separadas_por_exercicio(self) -> None:
        self.cache.obter((2026, "supabase"), self.calc, 300)
        valor, hit = self.cache.obter((2025, "supabase"), self.calc, 300)
        self.assertFalse(hit)
        self.assertEqual(self.chamadas, 2)

    def test_invalidar_descarta(self) -> None:
        self.cache.obter("k", self.calc, 300)
        self.cache.invalidar()
        self.assertEqual(self.cache.obter("k", self.calc, 300), ({"n": 2}, False))

    def test_gravacao_durante_calculo_nao_guarda_resultado_velho(self) -> None:
        def calc_com_gravacao():
            self.cache.invalidar()  # uma sincronização terminou enquanto o resumo era montado
            return self.calc()

        self.cache.obter("k", calc_com_gravacao, 300)
        _, hit = self.cache.obter("k", self.calc, 300)
        self.assertFalse(hit)

    def test_ttl_zero_desliga(self) -> None:
        self.cache.obter("k", self.calc, 0)
        self.assertEqual(self.cache.obter("k", self.calc, 0), ({"n": 2}, False))

    def test_pedidos_simultaneos_calculam_uma_vez(self) -> None:
        inicio = threading.Event()
        libera = threading.Event()

        def lento():
            inicio.set()
            libera.wait(2)
            return self.calc()

        resultados = []
        t1 = threading.Thread(target=lambda: resultados.append(self.cache.obter("k", lento, 300)))
        t1.start()
        inicio.wait(2)
        t2 = threading.Thread(target=lambda: resultados.append(self.cache.obter("k", lento, 300)))
        t2.start()
        libera.set()
        t1.join(2)
        t2.join(2)
        self.assertEqual(self.chamadas, 1)
        self.assertEqual(sorted(hit for _, hit in resultados), [False, True])


class TestInvalidacao(unittest.TestCase):
    def _post(self, rota: str) -> list:
        eventos = []
        fake = mock.Mock()
        fake._resolve_route.return_value = (rota, {})
        fake._do_post.side_effect = lambda path: eventos.append("post " + path)
        with mock.patch.object(web_server, "invalidar_cache_transparencia", side_effect=lambda: eventos.append("inv")):
            RadarAPIHandler.do_POST(fake)
        return eventos

    def test_sincronizacoes_invalidam_antes_e_depois(self) -> None:
        for rota in web_server.ROTAS_QUE_MUDAM_TRANSPARENCIA:
            self.assertEqual(self._post(rota), ["inv", "post " + rota, "inv"])

    def test_abrir_o_app_nao_invalida(self) -> None:
        for rota in ("/api/auto-atualizar/abrir", "/api/auto-atualizar", "/api/push/inscrever", "/api/executar"):
            self.assertEqual(self._post(rota), ["post " + rota])

    def test_tarefas_pncp_e_fiorilli_invalidam(self) -> None:
        with mock.patch.object(web_server, "invalidar_cache_transparencia") as inv, \
                mock.patch.object(web_server.web_push, "notificar_em_segundo_plano"):
            web_server._ao_concluir_tarefa("varredura", {})
            inv.assert_not_called()
            web_server._ao_concluir_tarefa("fiorilli", {})
            web_server._ao_concluir_tarefa("pncp", {})
            self.assertEqual(inv.call_count, 2)


if __name__ == "__main__":
    unittest.main()
