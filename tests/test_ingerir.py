"""Testes das funções puras e da aplicação de operações do ingest."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import ingerir
from tests.support import criar_repo, fonte

VAZIO_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class NormalizacaoTest(unittest.TestCase):
    def test_colapsa_espacos_e_minusculas(self):
        self.assertEqual(ingerir.normalizar("  Olá\n\tMUNDO  "), "olá mundo")

    def test_aplica_nfc(self):
        self.assertEqual(ingerir.normalizar("e\u0301"), "é")

    def test_hash_ignora_variacoes_de_espaco_e_caixa(self):
        self.assertEqual(ingerir.hash_conteudo("Olá  MUNDO"), ingerir.hash_conteudo(" olá mundo "))

    def test_hash_de_texto_vazio_tem_valor_conhecido(self):
        self.assertEqual(ingerir.hash_conteudo(""), VAZIO_SHA256)

    def test_hash_de_linha_tem_quatro_hexadecimais(self):
        valor = ingerir.hash_linha("---")
        self.assertRegex(valor, r"^[0-9a-f]{4}$")
        self.assertNotEqual(valor, ingerir.hash_linha("id: exemplo"))


class SourceIdTest(unittest.TestCase):
    def test_sem_colisao_usa_prefixo_e_data(self):
        self.assertEqual(ingerir.gerar_source_id("ata", "2030-04-10", set()), "ATA-2030-04-10")

    def test_colisao_cresce_o_sufixo_6_8_10(self):
        base = "ATA-2030-04-10"
        sid6 = ingerir.gerar_source_id("ata", "2030-04-10", {base}, "x")
        self.assertRegex(sid6, r"^ATA-2030-04-10-[0-9A-F]{6}$")
        self.assertEqual(sid6, "ATA-2030-04-10-2D7116")

        sid8 = ingerir.gerar_source_id("ata", "2030-04-10", {base, sid6}, "x")
        self.assertRegex(sid8, r"^ATA-2030-04-10-[0-9A-F]{8}$")
        self.assertNotEqual(sid8, sid6)

        sid10 = ingerir.gerar_source_id("ata", "2030-04-10", {base, sid6, sid8}, "x")
        self.assertRegex(sid10, r"^ATA-2030-04-10-[0-9A-F]{10}$")
        self.assertNotIn(sid10, {base, sid6, sid8})


class PiiTest(unittest.TestCase):
    def test_detecta_cpf(self):
        self.assertIn("cpf", ingerir.detectar_pii("CPF 123.456.789-09"))

    def test_detecta_ra(self):
        self.assertIn("ra", ingerir.detectar_pii("RA 1234567 do aluno"))

    def test_detecta_telefone(self):
        self.assertIn("telefone", ingerir.detectar_pii("Ligar para (11) 98765-4321"))

    def test_detecta_email_nao_institucional(self):
        self.assertIn("email", ingerir.detectar_pii("Contato: pessoa@gmail.com"))

    def test_aceita_email_institucional(self):
        self.assertEqual(ingerir.detectar_pii("Contato: secretaria@example.edu"), [])

    def test_texto_comum_nao_tem_pii(self):
        self.assertEqual(ingerir.detectar_pii("A assembleia aprovou o calendário."), [])


class ContextoTest(unittest.TestCase):
    def test_contexto_tem_paginas_linhas_numeradas_e_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            contexto = ingerir.montar_contexto_numerado(raiz)
        self.assertIn('<pagina caminho="wiki/processos/exemplo.md">', contexto)
        self.assertIn("L1 [hash=", contexto)
        self.assertIn(" id: exemplo", contexto)
        self.assertIn("</pagina>", contexto)


class AplicarOperacoesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        self.pagina = self.raiz / "wiki/processos/exemplo.md"

    def test_aplicar_linha_valida_e_acrescenta_fonte_ao_frontmatter(self):
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "texto": "Novo fato. [fonte: ATA-2030-03-10]",
            }
        ]
        resultado = ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})
        self.assertEqual(len(resultado["aplicadas"]), 1)
        texto = self.pagina.read_text(encoding="utf-8")
        self.assertIn("- Novo fato. [fonte: ATA-2030-03-10]", texto)
        self.assertIn("- Fato existente. [fonte: ATA-2030-03-10]", texto)

    def test_linha_sem_citacao_e_recusada(self):
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "texto": "Fato sem fonte.",
            }
        ]
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})
        self.assertNotIn("Fato sem fonte.", self.pagina.read_text(encoding="utf-8"))

    def test_citacao_de_fonte_desconhecida_e_recusada(self):
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "texto": "Fato inventado. [fonte: ATA-9999-99-99]",
            }
        ]
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})

    def test_operacao_nao_suportada_e_recusada_com_erro_claro(self):
        operacoes = [{"tipo": "marcar_substituida", "pagina": "wiki/processos/exemplo.md"}]
        with self.assertRaises(ingerir.ErroOperacao) as erro:
            ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})
        self.assertIn("marcar_substituida", str(erro.exception))
        self.assertIn("adicionar_linha", str(erro.exception))

    def test_hash_divergente_nao_aplica(self):
        linhas = self.pagina.read_text(encoding="utf-8").splitlines()
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "linha": 16,
                "hash": "0000",
                "texto": "Novo fato. [fonte: ATA-2030-03-10]",
            }
        ]
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})
        self.assertEqual(self.pagina.read_text(encoding="utf-8").splitlines(), linhas)

    def test_hash_correto_aplica_apos_a_linha_referenciada(self):
        linhas = self.pagina.read_text(encoding="utf-8").splitlines()
        indice = linhas.index("- Fato existente. [fonte: ATA-2030-03-10]")
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "linha": indice + 1,
                "hash": ingerir.hash_linha(linhas[indice]),
                "texto": "Novo fato. [fonte: ATA-2030-03-10]",
            }
        ]
        ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})
        novas = self.pagina.read_text(encoding="utf-8").splitlines()
        self.assertEqual(novas[indice + 1], "- Novo fato. [fonte: ATA-2030-03-10]")

    def test_pagina_fora_da_wiki_e_recusada(self):
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "../fora.md",
                "texto": "x [fonte: ATA-2030-03-10]",
            }
        ]
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})

    def test_falha_em_uma_operacao_nao_grava_outra(self):
        operacoes = [
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "texto": "Válida. [fonte: ATA-2030-03-10]",
            },
            {
                "tipo": "adicionar_linha",
                "pagina": "wiki/processos/exemplo.md",
                "secao": "O que as fontes dizem",
                "texto": "Sem fonte.",
            },
        ]
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(self.raiz, operacoes, {"ATA-2030-03-10"})
        self.assertNotIn("Válida.", self.pagina.read_text(encoding="utf-8"))


class FonteTest(unittest.TestCase):
    def test_rejeita_tipo_desconhecido(self):
        with self.assertRaises(ingerir.ErroFonte):
            ingerir.validar_fonte(fonte(tipo="oficio"))

    def test_rejeita_data_invalida(self):
        with self.assertRaises(ingerir.ErroFonte):
            ingerir.validar_fonte(fonte(data="2030-13-40"))

    def test_rejeita_texto_acima_do_limite(self):
        with self.assertRaises(ingerir.ErroFonte):
            ingerir.validar_fonte(fonte(texto="a" * (ingerir.MAX_BYTES + 1)))

    def test_rejeita_pii(self):
        with self.assertRaises(ingerir.ErroFonte):
            ingerir.validar_fonte(fonte(texto="CPF 123.456.789-09"))


class CompilarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)

    def test_compila_com_modelo_injetado_e_aplica_so_adicionar_linha(self):
        def modelo(prompt):
            self.assertIn("ATA-2030-04-10", prompt)
            self.assertIn('<pagina caminho="wiki/processos/exemplo.md">', prompt)
            return json.dumps(
                {
                    "fatos_novos": [],
                    "mudancas": [],
                    "contradicoes": [],
                    "lacunas": [],
                    "operacoes": [
                        {
                            "tipo": "adicionar_linha",
                            "pagina": "wiki/processos/exemplo.md",
                            "secao": "O que as fontes dizem",
                            "texto": "Novo fato. [fonte: ATA-2030-04-10]",
                        }
                    ],
                }
            )

        resultado = ingerir.compilar(fonte(), self.raiz, modelo)
        self.assertEqual(resultado["source_id"], "ATA-2030-04-10")
        self.assertEqual(len(resultado["aplicadas"]), 1)
        self.assertIn("ATA-2030-04-10", (self.raiz / "wiki/processos/exemplo.md").read_text(encoding="utf-8"))

    def test_resposta_sem_json_vira_erro_claro(self):
        with self.assertRaises(ingerir.ErroResposta):
            ingerir.compilar(fonte(), self.raiz, lambda prompt: "não é json")


if __name__ == "__main__":
    unittest.main()
