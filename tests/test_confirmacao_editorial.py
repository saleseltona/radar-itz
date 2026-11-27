"""
Testes da separação entre relevância editorial e confirmação factual.
"Confirmado" exige documento primário verificado + contraditório registrado
(e, para denúncias, decisão definitiva). Sem rede.
"""
from __future__ import annotations

import inspect
import unittest

import main
from src.evidence_verifier import (
    FALTA_CONTRADITORIO,
    FALTA_DOCUMENTO_PRIMARIO,
    EvidenceVerifier,
)
from src.live_news import LiveNewsFetcher, is_official_url
from src import report_generator

RESUMO_LONGO = (
    "A Prefeitura de Imperatriz anunciou nesta segunda-feira a ampliação do horário de atendimento "
    "das unidades básicas de saúde nos bairros Bacuri, Santa Rita e Vila Lobão."
)


class TestConfirmacaoEditorial(unittest.TestCase):
    def test_noticia_oficial_longa_nao_e_confirmada(self) -> None:
        meta = LiveNewsFetcher._classify_item(
            "Prefeitura amplia horário das UBS", RESUMO_LONGO, "2026-11-26",
            "Prefeitura de Imperatriz", "https://www.imperatriz.ma.gov.br/noticias/ubs",
        )
        self.assertEqual(meta["nivel_verificacao"], "Parcialmente confirmado")
        self.assertEqual(meta["tipo_fonte"], "Fonte oficial")
        # A confiabilidade da fonte continua pesando na relevância.
        self.assertEqual(meta["pontuacao_base"]["qualidade_evidencias"], 18)

    def test_noticia_policial_de_grande_veiculo_nao_e_confirmada(self) -> None:
        meta = LiveNewsFetcher._classify_item(
            "Polícia prende suspeito de homicídio em Imperatriz",
            "A Polícia Civil prendeu nesta manhã um homem suspeito de homicídio no bairro Bacuri, "
            "segundo informações preliminares repassadas pela delegacia regional.",
            "2026-11-26", "g1 MA", "https://g1.globo.com/ma/maranhao/noticia/2026/11/26/prisao.ghtml",
        )
        self.assertEqual(meta["categoria_sugerida"], "Pautas urgentes")
        self.assertNotEqual(meta["nivel_verificacao"], "Confirmado")

    def test_dominio_oficial_por_sufixo_exato(self) -> None:
        self.assertTrue(is_official_url("https://imperatriz.ma.gov.br/x"))
        self.assertTrue(is_official_url("https://www.gov.br/saude"))
        self.assertFalse(is_official_url("https://gov.br.exemplo.com/noticia"))
        self.assertFalse(is_official_url("https://falsogov.br/noticia"))
        self.assertFalse(is_official_url("https://exemplo.com/?ref=imperatriz.ma.gov.br"))
        meta = LiveNewsFetcher._classify_item(
            "Prefeitura amplia horário das UBS", RESUMO_LONGO, "2026-11-26",
            "Site falso", "https://gov.br.exemplo.com/noticia",
        )
        self.assertEqual(meta["tipo_fonte"], "Fonte secundária")
        self.assertEqual(meta["pontuacao_base"]["qualidade_evidencias"], 14)

    def test_item_coletado_registra_publicacao_e_nao_fato(self) -> None:
        item = LiveNewsFetcher._build_news_item(
            url="https://www.imperatriz.ma.gov.br/noticias/ubs", titulo="Prefeitura amplia horário das UBS",
            desc=RESUMO_LONGO, data_pub="2026-11-26", source_name="Prefeitura de Imperatriz",
        )
        self.assertEqual(item["fatos_confirmados"], [])
        self.assertTrue(item["registros_publicacao"])
        self.assertTrue(item["informacoes_pendentes"])
        verif = EvidenceVerifier.verify_and_classify(item)
        self.assertEqual(verif["nivel_verificacao"], "Parcialmente confirmado")
        self.assertIn("[Registro de publicação]", verif["evidencias_verificadas"])
        self.assertNotIn("[Fato objetivo]", verif["evidencias_verificadas"])

    def test_confirmado_vindo_da_coleta_e_rebaixado(self) -> None:
        pauta = {
            "titulo": "Edital de pregão publicado",
            "categoria_sugerida": "Pautas de serviço público",
            "nivel_verificacao": "Confirmado",
            "fatos_confirmados": ["Edital publicado"],
            "documentos_citados": ["Pregão nº 036/2026"],
        }
        verif = EvidenceVerifier.verify_and_classify(pauta)
        self.assertEqual(verif["nivel_verificacao"], "Parcialmente confirmado")
        self.assertIn(FALTA_DOCUMENTO_PRIMARIO, verif["pendencias_confirmacao"])
        self.assertIn(FALTA_CONTRADITORIO, verif["pendencias_confirmacao"])
        # Identificador extraído do texto não é documento primário consultado.
        self.assertIn("[Documento citado, não consultado]: Pregão nº 036/2026", verif["evidencias_verificadas"])

    def test_so_documento_sem_contraditorio_nao_confirma(self) -> None:
        verif = EvidenceVerifier.verify_and_classify({
            "titulo": "Edital", "nivel_verificacao": "Confirmado",
            "documentos_primarios_verificados": ["https://diariooficial.imperatriz.ma.gov.br/edicao/123"],
        })
        self.assertEqual(verif["nivel_verificacao"], "Parcialmente confirmado")
        self.assertIn(FALTA_CONTRADITORIO, verif["pendencias_confirmacao"])

    def test_documento_e_contraditorio_confirmam(self) -> None:
        verif = EvidenceVerifier.verify_and_classify({
            "titulo": "Prefeitura amplia horário das UBS",
            "categoria_sugerida": "Pautas de serviço público",
            "documentos_primarios_verificados": ["Decreto nº 10/2026, Diário Oficial de 26/11/2026"],
            "contraditorio_registrado": ["SEMUS confirmou por nota em 26/11/2026"],
        })
        self.assertEqual(verif["nivel_verificacao"], "Confirmado")
        self.assertEqual(verif["pendencias_confirmacao"], "")
        self.assertIn("[Documento primário verificado]", verif["evidencias_verificadas"])
        self.assertIn("[Contraditório]", verif["evidencias_verificadas"])

    def test_denuncia_exige_decisao_definitiva(self) -> None:
        base = {
            "titulo": "TCE suspende contrato de pavimentação",
            "categoria_sugerida": "Pautas de denúncia e apuração",
            "nivel_verificacao": "Confirmado",
            "documentos_primarios_verificados": ["Decisão cautelar TCE-MA nº 1/2026"],
            "contraditorio_registrado": ["Prefeitura enviou nota"],
            "fatos_confirmados": ["Decisão cautelar publicada"],
        }
        self.assertEqual(EvidenceVerifier.verify_and_classify(base)["nivel_verificacao"], "Parcialmente confirmado")
        base["sentenca_definitiva_transitada"] = True
        self.assertEqual(EvidenceVerifier.verify_and_classify(base)["nivel_verificacao"], "Confirmado")
        sem_base = {"titulo": "Denúncia de fraude", "categoria_sugerida": "Pautas de denúncia e apuração"}
        self.assertEqual(EvidenceVerifier.verify_and_classify(sem_base)["nivel_verificacao"], "Exige apuração")

    def test_textos_fixos_nao_afirmam_confirmacao(self) -> None:
        fonte_main = inspect.getsource(main)
        self.assertNotIn("evidências primárias confirmadas", fonte_main)
        self.assertNotIn("executou com sucesso", fonte_main)
        src = inspect.getsource(report_generator)
        self.assertNotIn("impacto direto na segurança/saúde coletiva", src)


if __name__ == "__main__":
    unittest.main()
