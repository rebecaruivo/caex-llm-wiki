"""Testes dos guardrails completos do compilador (ticket #2)."""

import json
import tempfile
import unittest
from pathlib import Path

import ingerir
from tests.support import criar_repo, fonte
from tools.lint_wiki import revisar


def registrar_fonte(
    raiz,
    source_id="ATA-2030-04-10",
    tipo="ata",
    autoridade="ca-deliberativa",
    data="2030-04-10",
    arquivo="sources/atas/ata.md",
):
    caminho = raiz / "sources/manifest.json"
    manifesto = json.loads(caminho.read_text(encoding="utf-8"))
    manifesto["fontes"].append(
        {
            "id": source_id,
            "titulo": "Fonte de teste",
            "tipo": tipo,
            "autoridade": autoridade,
            "data": data,
            "arquivo": arquivo,
            "uso": "wiki",
        }
    )
    caminho.write_text(json.dumps(manifesto), encoding="utf-8")


def referencia(raiz, texto="- Fato existente. [fonte: ATA-2030-03-10]"):
    linhas = (raiz / "wiki/processos/exemplo.md").read_text(encoding="utf-8").splitlines()
    indice = linhas.index(texto)
    return indice + 1, ingerir.hash_linha(linhas[indice])


def criar_glossario(raiz):
    (raiz / "wiki/glossario.md").write_text(
        """---
id: glossario
titulo: Glossário
tipo: referencia
status: rascunho
confianca: alta
fontes: [ATA-2030-03-10]
atualizado_em: 2030-03-10
validado_por: null
validado_em: null
---

# Glossário

- **CAEX:** centro acadêmico. [fonte: ATA-2030-03-10]
""",
        encoding="utf-8",
    )
    indice = raiz / "wiki/index.md"
    texto = indice.read_text(encoding="utf-8")
    if "glossario.md" not in texto:
        texto = texto.replace(
            "## Manutenção", "- [Glossário](glossario.md)\n\n## Manutenção"
        )
        indice.write_text(texto, encoding="utf-8")


def criar_contatos(raiz):
    (raiz / "wiki/contatos.md").write_text(
        """---
id: contatos
titulo: Contatos
tipo: referencia
status: rascunho
confianca: alta
fontes: [ATA-2030-03-10]
atualizado_em: 2030-03-10
validado_por: null
validado_em: null
---

# Contatos

| Setor | Contato | Fonte |
|---|---|---|
| Secretaria | secretaria@example.edu | [fonte: ATA-2030-03-10] |
""",
        encoding="utf-8",
    )
    indice = raiz / "wiki/index.md"
    texto = indice.read_text(encoding="utf-8")
    if "contatos.md" not in texto:
        texto = texto.replace(
            "## Manutenção", "- [Contatos](contatos.md)\n\n## Manutenção"
        )
        indice.write_text(texto, encoding="utf-8")


class SubstituicaoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        self.pagina = self.raiz / "wiki/processos/exemplo.md"
        self.validos = {"ATA-2030-03-10", "ATA-2030-04-10"}

    def operacao_marcar(self, **mudancas):
        linha, hash_ = referencia(self.raiz)
        operacao = {
            "tipo": "marcar_substituida",
            "pagina": "wiki/processos/exemplo.md",
            "linha": linha,
            "hash": hash_,
            "nova_fonte": "ATA-2030-04-10",
            "data": "2030-04-10",
        }
        operacao.update(mudancas)
        return operacao

    def test_marcar_acrescenta_marca_sem_apagar_linha(self):
        antes = self.pagina.read_text(encoding="utf-8").splitlines()
        resultado = ingerir.aplicar_operacoes(
            self.raiz,
            [self.operacao_marcar()],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        depois = self.pagina.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(depois), len(antes))
        alvo = "- Fato existente. [fonte: ATA-2030-03-10]"
        self.assertIn(
            alvo + " (substituída por [fonte: ATA-2030-04-10] em 2030-04-10)",
            depois,
        )
        self.assertEqual(resultado["substituicoes"], 1)

    def test_hash_divergente_nao_altera(self):
        antes = self.pagina.read_text(encoding="utf-8")
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [self.operacao_marcar(hash="0000")],
                self.validos,
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )
        self.assertEqual(self.pagina.read_text(encoding="utf-8"), antes)

    def test_autoridade_menor_recusa(self):
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [self.operacao_marcar(nova_fonte="GUIA-2030-04-10")],
                {"ATA-2030-03-10", "GUIA-2030-04-10"},
                fonte=fonte(tipo="guia"),
                source_id="GUIA-2030-04-10",
                data_documento="2030-04-10",
            )
        self.assertNotIn(
            "substituída", self.pagina.read_text(encoding="utf-8")
        )

    def test_fonte_nao_confiavel_nunca_sustenta(self):
        registrar_fonte(
            self.raiz,
            source_id="SUG-2030-04-10",
            tipo="outro",
            autoridade="nao-confiavel",
        )
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [self.operacao_marcar(nova_fonte="SUG-2030-04-10")],
                {"ATA-2030-03-10", "SUG-2030-04-10"},
                data_documento="2030-04-10",
            )
        self.assertNotIn("substituída", self.pagina.read_text(encoding="utf-8"))

    def test_data_igual_ou_menor_recusa(self):
        for data in ("2030-03-10", "2030-03-09"):
            with self.assertRaises(ingerir.ErroOperacao):
                ingerir.aplicar_operacoes(
                    self.raiz,
                    [self.operacao_marcar(data=data)],
                    self.validos,
                    fonte=fonte(data=data),
                    source_id="ATA-2030-04-10",
                    data_documento=data,
                )
        self.assertNotIn("substituída", self.pagina.read_text(encoding="utf-8"))

    def test_atualizar_resumo_exige_substituicao(self):
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [
                    {
                        "tipo": "atualizar_resumo",
                        "pagina": "wiki/processos/exemplo.md",
                        "texto": "Resumo novo.",
                    }
                ],
                self.validos,
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )

    def test_atualizar_resumo_com_substituicao_valida(self):
        ingerir.aplicar_operacoes(
            self.raiz,
            [
                self.operacao_marcar(),
                {
                    "tipo": "atualizar_resumo",
                    "pagina": "wiki/processos/exemplo.md",
                    "texto": "Resumo atualizado pela substituição.",
                },
            ],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        pagina = self.pagina.read_text(encoding="utf-8")
        self.assertIn("Resumo atualizado pela substituição.", pagina)
        self.assertIn("substituída por", pagina)


class PaginaNovaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        registrar_fonte(self.raiz)
        self.validos = {"ATA-2030-03-10", "ATA-2030-04-10"}

    def operacao(self, **mudancas):
        operacao = {
            "tipo": "criar_pagina",
            "caminho": "wiki/decisoes/nova-decisao.md",
            "tipo_pagina": "decisao",
            "titulo": "Nova decisão",
            "secoes": {
                "Resumo": ["Resumo da decisão. [fonte: ATA-2030-04-10]"],
                "O que as fontes dizem": ["Fato novo. [fonte: ATA-2030-04-10]"],
                "O que ainda não sabemos": ["Ainda falta detalhe."],
                "Quem procurar": ["Secretaria."],
                "Páginas relacionadas": ["[Índice](../index.md)"],
            },
        }
        operacao.update(mudancas)
        return operacao

    def test_pagina_nova_de_decisao_monta_esqueleto_e_lint(self):
        ingerir.aplicar_operacoes(
            self.raiz,
            [self.operacao()],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        pagina = (self.raiz / "wiki/decisoes/nova-decisao.md").read_text(encoding="utf-8")
        self.assertIn("id: nova-decisao", pagina)
        self.assertIn("tipo: decisao", pagina)
        self.assertIn("confianca: baixa", pagina)
        self.assertIn("status: rascunho", pagina)
        self.assertIn("validado_por: null", pagina)
        self.assertIn("validado_em: null", pagina)
        self.assertIn("atualizado_em: 2030-04-10", pagina)
        self.assertIn("fontes: [ATA-2030-04-10]", pagina)
        self.assertNotIn("## Passo a passo", pagina)
        for secao in (
            "## Resumo",
            "## O que as fontes dizem",
            "## O que ainda não sabemos",
            "## Quem procurar",
            "## Páginas relacionadas",
        ):
            self.assertIn(secao, pagina)
        indice = (self.raiz / "wiki/index.md").read_text(encoding="utf-8")
        self.assertIn("(decisoes/nova-decisao.md)", indice)
        self.assertEqual(revisar(self.raiz)["erros"], [])

    def test_pagina_nova_de_processo_tem_passo_a_passo_entre_2_e_3(self):
        operacao = self.operacao(
            caminho="wiki/processos/novo-processo.md",
            tipo_pagina="processo",
            secoes={
                "Resumo": ["Resumo do processo."],
                "O que as fontes dizem": ["Fato. [fonte: ATA-2030-04-10]"],
                "Passo a passo": ["Primeiro passo. [fonte: ATA-2030-04-10]"],
                "O que ainda não sabemos": ["Falta o resto."],
                "Quem procurar": ["Secretaria."],
                "Páginas relacionadas": ["[Índice](../index.md)"],
            },
        )
        ingerir.aplicar_operacoes(
            self.raiz,
            [operacao],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        pagina = (self.raiz / "wiki/processos/novo-processo.md").read_text(encoding="utf-8")
        posicoes = [pagina.index(s) for s in (
            "## Resumo",
            "## O que as fontes dizem",
            "## Passo a passo",
            "## O que ainda não sabemos",
            "## Quem procurar",
            "## Páginas relacionadas",
        )]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertEqual(revisar(self.raiz)["erros"], [])

    def test_pagina_nova_relato_e_recusada(self):
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [self.operacao(tipo_pagina="relato")],
                self.validos,
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )
        self.assertFalse((self.raiz / "wiki/decisoes/nova-decisao.md").exists())

    def test_pagina_nova_de_fonte_relato_e_recusada(self):
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [self.operacao()],
                self.validos,
                fonte=fonte(tipo="relato"),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )
        self.assertFalse((self.raiz / "wiki/decisoes/nova-decisao.md").exists())

    def test_pagina_nova_fora_das_pastas_e_recusada(self):
        for caminho in ("wiki/relatos/novo.md", "wiki/nova.md", "wiki/decisoes/../x.md"):
            with self.assertRaises(ingerir.ErroOperacao):
                ingerir.aplicar_operacoes(
                    self.raiz,
                    [self.operacao(caminho=caminho)],
                    self.validos,
                    fonte=fonte(),
                    source_id="ATA-2030-04-10",
                    data_documento="2030-04-10",
                )

    def test_tipo_da_pagina_precisa_bater_com_a_pasta(self):
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [self.operacao(tipo_pagina="processo")],
                self.validos,
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )

    def test_fontes_derivado_exatamente_das_citacoes_do_corpo(self):
        operacao = self.operacao(
            caminho="wiki/decisoes/outra.md",
            secoes={
                "Resumo": ["Resumo sem citação."],
                "O que as fontes dizem": ["Fato. [fonte: ATA-2030-03-10]"],
                "O que ainda não sabemos": ["Lacuna."],
                "Quem procurar": ["Secretaria."],
                "Páginas relacionadas": ["[Índice](../index.md)"],
            },
        )
        ingerir.aplicar_operacoes(
            self.raiz,
            [operacao],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        pagina = (self.raiz / "wiki/decisoes/outra.md").read_text(encoding="utf-8")
        self.assertIn("fontes: [ATA-2030-03-10]", pagina)

    def test_titulo_novo_e_sanitizado_contra_nomes(self):
        ingerir.aplicar_operacoes(
            self.raiz,
            [self.operacao(titulo="Rosa coordena a decisão")],
            self.validos,
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
            nomes=["Rosa"],
        )
        pagina = (self.raiz / "wiki/decisoes/nova-decisao.md").read_text(encoding="utf-8")
        self.assertNotIn("Rosa", pagina)
        self.assertIn("[nome removido] coordena a decisão", pagina)

    def test_pagina_nova_sem_citacao_na_janela_e_recusada(self):
        operacao = self.operacao(
            secoes={
                "Resumo": ["Resumo."],
                "O que as fontes dizem": ["Fato sem fonte."],
                "O que ainda não sabemos": ["Lacuna."],
                "Quem procurar": ["Secretaria."],
                "Páginas relacionadas": ["[Índice](../index.md)"],
            }
        )
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [operacao],
                self.validos,
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )


class CitacaoJanelaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        self.pagina = self.raiz / "wiki/processos/exemplo.md"

    def test_passo_a_passo_sem_citacao_e_recusado(self):
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "Passo a passo",
                        "texto": "Passo sem fonte.",
                    }
                ],
                {"ATA-2030-03-10"},
            )
        self.assertNotIn("Passo sem fonte.", self.pagina.read_text(encoding="utf-8"))

    def test_adicionar_por_linha_na_janela_exige_citacao(self):
        linha, hash_ = referencia(self.raiz)
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "linha": linha,
                        "hash": hash_,
                        "texto": "Inserção sem fonte.",
                    }
                ],
                {"ATA-2030-03-10"},
            )

    def test_linha_em_arquivo_escrito_pelo_codigo_e_recusada(self):
        for pagina in ("wiki/index.md", "wiki/log.md", "wiki/pendencias.md"):
            with self.assertRaises(ingerir.ErroOperacao):
                ingerir.aplicar_operacoes(
                    self.raiz,
                    [
                        {
                            "tipo": "adicionar_linha",
                            "pagina": pagina,
                            "secao": "O que as fontes dizem",
                            "texto": "Tentativa. [fonte: ATA-2030-03-10]",
                        }
                    ],
                    {"ATA-2030-03-10"},
                )


class PendenciasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)
        registrar_fonte(self.raiz)
        self.pendencias = self.raiz / "wiki/pendencias.md"

    def test_contradicao_gera_p_xx_citando_a_fonte(self):
        resultado = ingerir.aplicar_operacoes(
            self.raiz,
            [],
            {"ATA-2030-03-10", "ATA-2030-04-10"},
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
            contradicoes=[
                {
                    "pagina": "wiki/processos/exemplo.md",
                    "linha": 1,
                    "resumo": "A data diverge da ata anterior.",
                    "fontes": ["ATA-2030-04-10"],
                }
            ],
        )
        texto = self.pendencias.read_text(encoding="utf-8")
        self.assertIn("P-01", texto)
        self.assertIn("A data diverge da ata anterior.", texto)
        self.assertIn("[fonte: ATA-2030-04-10]", texto)
        self.assertIn("ATA-2030-04-10", texto.split("---", 2)[1])
        self.assertEqual(resultado["pendencias"][0]["id"], "P-01")
        self.assertEqual(revisar(self.raiz)["erros"], [])

    def test_contradicao_sem_fonte_falha_e_nao_escreve(self):
        antes = self.pendencias.read_text(encoding="utf-8")
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [],
                {"ATA-2030-04-10"},
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
                contradicoes=[
                    {
                        "pagina": "wiki/processos/exemplo.md",
                        "linha": 1,
                        "resumo": "Contradição sem fonte.",
                        "fontes": [],
                    }
                ],
            )
        self.assertEqual(self.pendencias.read_text(encoding="utf-8"), antes)

    def test_glossario_pode_receber_linha_citada(self):
        criar_glossario(self.raiz)
        glossario = self.raiz / "wiki/glossario.md"
        ingerir.aplicar_operacoes(
            self.raiz,
            [
                {
                    "tipo": "adicionar_linha",
                    "pagina": "wiki/glossario.md",
                    "texto": "**GT:** grupo de trabalho. [fonte: ATA-2030-04-10]",
                }
            ],
            {"ATA-2030-03-10", "ATA-2030-04-10"},
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        texto = glossario.read_text(encoding="utf-8")
        self.assertIn("- **GT:** grupo de trabalho. [fonte: ATA-2030-04-10]", texto)
        self.assertIn("ATA-2030-04-10", texto.split("---", 2)[1])
        self.assertEqual(revisar(self.raiz)["erros"], [])

    def test_glossario_sem_citacao_e_recusado(self):
        criar_glossario(self.raiz)
        with self.assertRaises(ingerir.ErroOperacao):
            ingerir.aplicar_operacoes(
                self.raiz,
                [
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/glossario.md",
                        "texto": "**GT:** grupo de trabalho.",
                    }
                ],
                {"ATA-2030-03-10", "ATA-2030-04-10"},
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )

    def test_contatos_vira_pendencia_sem_editar(self):
        criar_contatos(self.raiz)
        contatos = self.raiz / "wiki/contatos.md"
        antes = contatos.read_text(encoding="utf-8")
        ingerir.aplicar_operacoes(
            self.raiz,
            [
                {
                    "tipo": "adicionar_linha",
                    "pagina": "wiki/contatos.md",
                    "secao": "Contatos",
                    "texto": "Novo contato. [fonte: ATA-2030-04-10]",
                }
            ],
            {"ATA-2030-03-10", "ATA-2030-04-10"},
            fonte=fonte(),
            source_id="ATA-2030-04-10",
            data_documento="2030-04-10",
        )
        self.assertEqual(contatos.read_text(encoding="utf-8"), antes)
        self.assertIn("contato", self.pendencias.read_text(encoding="utf-8").lower())


class FrontmatterTest(unittest.TestCase):
    def test_pagina_tocada_volta_a_rascunho_e_atualiza_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            pagina = raiz / "wiki/processos/exemplo.md"
            texto = pagina.read_text(encoding="utf-8")
            texto = texto.replace("status: rascunho", "status: validado")
            texto = texto.replace("validado_por: null", "validado_por: Gestão")
            texto = texto.replace("validado_em: null", "validado_em: 2030-03-11")
            texto = texto.replace("atualizado_em: 2030-03-10", "atualizado_em: 2030-03-05")
            pagina.write_text(texto, encoding="utf-8")
            ingerir.aplicar_operacoes(
                raiz,
                [
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "O que as fontes dizem",
                        "texto": "Novo fato. [fonte: ATA-2030-04-10]",
                    }
                ],
                {"ATA-2030-03-10", "ATA-2030-04-10"},
                fonte=fonte(),
                source_id="ATA-2030-04-10",
                data_documento="2030-04-10",
            )
            depois = pagina.read_text(encoding="utf-8")
        self.assertIn("status: rascunho", depois)
        self.assertIn("validado_por: null", depois)
        self.assertIn("validado_em: null", depois)
        self.assertIn("confianca: alta", depois)
        self.assertIn("atualizado_em: 2030-04-10", depois)


class NomeTest(unittest.TestCase):
    def test_nome_em_corpo_de_operacao_rejeita_ao_aplicar(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            with self.assertRaises(ingerir.ErroPii):
                ingerir.aplicar_operacoes(
                    raiz,
                    [
                        {
                            "tipo": "adicionar_linha",
                            "pagina": "wiki/processos/exemplo.md",
                            "secao": "O que as fontes dizem",
                            "texto": "Rosa aprovou. [fonte: ATA-2030-03-10]",
                        }
                    ],
                    {"ATA-2030-03-10"},
                    nomes=["Rosa"],
                )

    def test_bloqueio_e_sem_maiusculas_e_por_palavra_inteira(self):
        self.assertTrue(ingerir.contem_nome("Rosa aprovou", ["Rosa"]))
        self.assertTrue(ingerir.contem_nome("ROSA aprovou", ["Rosa"]))
        self.assertTrue(ingerir.contem_nome("camiseta rosa", ["Rosa"]))
        self.assertFalse(ingerir.contem_nome("Rosaflor", ["Rosa"]))


def resposta(**mudancas):
    dados = {
        "fatos_novos": [],
        "mudancas": [],
        "contradicoes": [],
        "lacunas": [],
        "operacoes": [],
    }
    dados.update(mudancas)
    return json.dumps(dados)


class TentativasTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raiz = criar_repo(self.tmp.name)

    def test_segunda_tentativa_recebe_lista_exata_de_erros(self):
        prompts = []

        def modelo(prompt):
            prompts.append(prompt)
            if len(prompts) == 1:
                return resposta(
                    operacoes=[
                        {
                            "tipo": "adicionar_linha",
                            "pagina": "wiki/processos/exemplo.md",
                            "secao": "O que as fontes dizem",
                            "texto": "Fato sem fonte.",
                        }
                    ]
                )
            return resposta(
                operacoes=[
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "O que as fontes dizem",
                        "texto": "Fato com fonte. [fonte: ATA-2030-04-10]",
                    }
                ]
            )

        resultado = ingerir.compilar(fonte(), self.raiz, modelo)
        self.assertEqual(resultado["tentativas"], 2)
        self.assertEqual(len(prompts), 2)
        self.assertIn("Erros da tentativa anterior", prompts[1])
        self.assertIn("citação", prompts[1])

    def test_tres_falhas_de_compilacao_terminam_com_motivo(self):
        chamadas = []

        def modelo(prompt):
            chamadas.append(prompt)
            return resposta(
                operacoes=[
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "O que as fontes dizem",
                        "texto": "Fato sem fonte.",
                    }
                ]
            )

        with self.assertRaises(ingerir.ErroCompilacao) as erro:
            ingerir.compilar(fonte(), self.raiz, modelo)
        self.assertEqual(erro.exception.motivo, "compilacao")
        self.assertEqual(len(erro.exception.erros), 1)
        self.assertIn("citação", erro.exception.erros[0])
        self.assertEqual(len(chamadas), 3)

    def test_json_invalido_termina_com_motivo_schema(self):
        with self.assertRaises(ingerir.ErroCompilacao) as erro:
            ingerir.compilar(fonte(), self.raiz, lambda prompt: "não é json")
        self.assertEqual(erro.exception.motivo, "schema")
        self.assertEqual(len(erro.exception.erros), 1)

    def test_lint_falhando_termina_com_motivo_lint(self):
        def modelo(prompt):
            return resposta(
                operacoes=[
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "Quem procurar",
                        "texto": "[Link quebrado](nao-existe.md) [fonte: ATA-2030-03-10]",
                    }
                ]
            )

        with self.assertRaises(ingerir.ErroCompilacao) as erro:
            ingerir.compilar(fonte(), self.raiz, modelo)
        self.assertEqual(erro.exception.motivo, "lint")
        self.assertTrue(
            any("link" in mensagem.lower() for mensagem in erro.exception.erros)
        )

    def test_nome_em_texto_gerado_termina_com_motivo_pii(self):
        def modelo(prompt):
            return resposta(
                operacoes=[
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "O que as fontes dizem",
                        "texto": "Rosa aprovou. [fonte: ATA-2030-04-10]",
                    }
                ]
            )

        with self.assertRaises(ingerir.ErroCompilacao) as erro:
            ingerir.compilar(fonte(), self.raiz, modelo, nomes=["Rosa"])
        self.assertEqual(erro.exception.motivo, "pii")
        self.assertIn("nome", erro.exception.erros[0].lower())

    def test_falha_nao_deixa_arquivos_pela_metade(self):
        antes = (self.raiz / "wiki/processos/exemplo.md").read_text(encoding="utf-8")

        def modelo(prompt):
            return resposta(
                operacoes=[
                    {
                        "tipo": "adicionar_linha",
                        "pagina": "wiki/processos/exemplo.md",
                        "secao": "Quem procurar",
                        "texto": "[Link quebrado](nao-existe.md) [fonte: ATA-2030-03-10]",
                    }
                ]
            )

        with self.assertRaises(ingerir.ErroCompilacao):
            ingerir.compilar(fonte(), self.raiz, modelo)
        self.assertEqual(
            (self.raiz / "wiki/processos/exemplo.md").read_text(encoding="utf-8"),
            antes,
        )


if __name__ == "__main__":
    unittest.main()
