"""Rotas públicas e contrato de API consumido pelas páginas de envio."""
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.responses import FileResponse

import app
import config_ingestao
import db
import ingerir

RAIZ = Path(__file__).resolve().parents[1]
STATIC = RAIZ / "static"


class FakeRequest:
    def __init__(self, ip="203.0.113.7", xff=None):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = SimpleNamespace(host=ip)


class FakeDB:
    """Supabase em memória com só o que as rotas públicas usam."""

    ErroBanco = db.ErroBanco
    DuplicataIngest = db.DuplicataIngest
    SourceIdEmUso = db.SourceIdEmUso

    def __init__(self):
        self.jobs = []
        self.limites = {}
        self.eventos = []
        self.buscas = 0

    def configurado(self):
        return True

    def consumir(self, chave, limite):
        contagem = self.limites.get(chave, 0)
        if contagem >= limite:
            return None
        self.limites[chave] = contagem + 1
        return self.limites[chave]

    def consumir_ip(self, chave, dia, limite, expira_em):
        return self.consumir(chave, limite)

    def consumir_global(self, chave, dia, limite, expira_em):
        return self.consumir(chave, limite)

    def consumir_status(self, chave, dia, limite, expira_em):
        return self.consumir(chave, limite)

    def buscar_job_bloqueante_por_hash(self, hash_, statuss):
        for job in self.jobs:
            if job.get("hash_conteudo") == hash_ and job.get("status") in statuss:
                return {"id": job["id"], "source_id": job["source_id"]}
        return None

    def buscar_hash_ingest(self, hash_, resultados):
        return None

    def listar_source_ids_ingest(self):
        return [job["source_id"] for job in self.jobs]

    def criar_job_ingest(self, dados):
        job = dict(dados)
        job["id"] = str(uuid4())
        job["criado_em"] = "2026-11-08T12:00:00Z"
        job["atualizado_em"] = job["criado_em"]
        self.jobs.append(job)
        return job

    def buscar_job_ingest(self, job_id):
        self.buscas += 1
        for job in self.jobs:
            if job["id"] == str(job_id):
                return job
        return None

    def contar_jobs_na_fila_antes(self, criado_em):
        return 0

    def job_mais_antigo_na_fila(self):
        fila = [job for job in self.jobs if job.get("status") == "na_fila"]
        return fila[0] if fila else None

    def tomar_lock_fila(self, job_id, intervalo_segundos):
        return True

    def liberar_lock_fila(self, job_id):
        return None

    def atualizar_job_ingest(self, job_id, campos):
        for job in self.jobs:
            if job["id"] == str(job_id):
                job.update(campos)
                return job
        return None

    def registrar_evento_ingest(self, job_id, evento, detalhe=None):
        self.eventos.append({"job_id": str(job_id), "evento": evento})


def dados_envio(**mudancas):
    dados = {
        "tipo": "ata",
        "titulo": "Ata de reuniao de 10/04/2030",
        "data_documento": "2030-04-10",
        "texto": "A assembleia aprovou o novo calendario e registrou a decisao.",
        "turnstile_token": "token-ok",
    }
    dados.update(mudancas)
    return dados


class TestRotasDasPaginas(unittest.TestCase):
    def test_enviar_entrega_html_sem_cache(self):
        resposta = app.pagina_enviar()
        self.assertIsInstance(resposta, FileResponse)
        self.assertEqual(Path(resposta.path).name, "enviar.html")
        self.assertEqual(resposta.headers.get("cache-control"), "no-cache")

    def test_andamento_entrega_o_mesmo_html_sem_cache(self):
        resposta = app.pagina_andamento(str(uuid4()))
        self.assertIsInstance(resposta, FileResponse)
        self.assertEqual(Path(resposta.path).name, "enviar.html")
        self.assertEqual(resposta.headers.get("cache-control"), "no-cache")

    def test_rotas_registradas_sem_quebrar_estaticos(self):
        caminhos = {rota.path for rota in app.app.routes}
        self.assertIn("/enviar", caminhos)
        self.assertIn("/enviar/{job_id}", caminhos)
        self.assertIn("/static", caminhos)
        self.assertIn("/", caminhos)
        self.assertIn("/revisar", caminhos)

    def test_paginas_existentes_continuam_iguais(self):
        self.assertEqual(Path(app.interface().path).name, "index.html")
        self.assertEqual(Path(app.pagina_revisao().path).name, "revisar.html")


class TestPaginaEnviar(unittest.TestCase):
    def setUp(self):
        self.caminho = STATIC / "enviar.html"
        self.texto = self.caminho.read_text(encoding="utf-8")

    def test_textos_obrigatorios(self):
        for trecho in (
            "Adicionar fonte à wiki",
            "Envie uma ata, um guia ou um relato.",
            "A IA propõe mudanças na wiki.",
            "O lint confere estrutura e citações.",
            "A gestão revisa o Pull Request.",
            "Se aprovado, a wiki é atualizada.",
            "Envio fechado para testes.",
            "Enviar fonte",
            "Não envie CPF, RA, telefone, e-mail pessoal ou nomes de pessoas.",
            "Declaro que removi dados pessoais e que o texto pode ser publicado nesta wiki após revisão da gestão.",
        ):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, self.texto)

    def test_campos_e_tipos(self):
        for trecho in (
            'id="tipo"',
            'id="titulo"',
            'type="date"',
            'id="texto"',
            'id="arquivo"',
            'id="consentimento"',
            'accept=".md,.txt,text/markdown,text/plain"',
            '<option value="ata">Ata</option>',
            '<option value="guia">Guia</option>',
            '<option value="outro">Relato</option>',
        ):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, self.texto)

    def test_le_arquivo_no_navegador_e_respeita_limite(self):
        self.assertIn("arquivo.text()", self.texto)
        self.assertIn("51200", self.texto)
        self.assertIn("max_bytes", self.texto)

    def test_polling_e_estados_no_mesmo_arquivo(self):
        for trecho in (
            "/api/ingest/",
            "5000",
            "15000",
            "aguardando_revisao",
            "processando",
            "na_fila",
            "publicado",
            "Este envio não está mais disponível.",
            "Atualizar",
            "posicao_fila",
            "source_id",
            "pr_url",
        ):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, self.texto)

    def test_monta_dom_sem_innerhtml(self):
        self.assertNotIn("innerHTML", self.texto)
        self.assertIn("createElement", self.texto)
        self.assertIn("textContent", self.texto)
        self.assertIn('aria-live="polite"', self.texto)

    def test_foco_no_titulo_e_rotulo_acessivel(self):
        self.assertIn('tabindex="-1"', self.texto)
        self.assertIn(".focus()", self.texto)
        self.assertIn('aria-current="page"', self.texto)

    def test_widget_turnstile_invisivel_so_quando_aberto(self):
        self.assertIn('id="turnstile-widget"', self.texto)
        self.assertIn("turnstile.render", self.texto)
        self.assertIn('size: "invisible"', self.texto)
        self.assertIn("api.js?render=explicit", self.texto)
        self.assertIn("obterTokenTurnstile", self.texto)
        self.assertIn("turnstile_token", self.texto)

    def test_widget_nao_carrega_na_pagina_fechada(self):
        self.assertNotIn('src="https://challenges.cloudflare.com/turnstile', self.texto)
        fechado = self.texto.index("config.aberto !== true")
        abre_formulario = self.texto.index("form.hidden = false")
        prepara = self.texto.index("prepararTurnstile(config.turnstile_site_key)")
        self.assertLess(fechado, abre_formulario)
        self.assertLess(abre_formulario, prepara)
        self.assertIn("form.hidden = true", self.texto[:prepara])


class TestNavegacaoComum(unittest.TestCase):
    def test_link_adicionar_fonte_em_todas_as_paginas(self):
        for nome in ("index.html", "revisar.html", "enviar.html"):
            with self.subTest(pagina=nome):
                texto = (STATIC / nome).read_text(encoding="utf-8")
                self.assertIn('href="/enviar"', texto)


class ContratoBase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        db_patch = patch.object(app, "db", self.fake)
        db_patch.start()
        self.addCleanup(db_patch.stop)

        self.env = {
            "TURNSTILE_SITE_KEY": "site-key",
            "TURNSTILE_SECRET_KEY": "secret-key",
            "INGEST_SALT": "sal-de-teste",
            "GITHUB_DISPATCH_TOKEN": "token-de-teste",
        }
        env_patch = patch.dict(os.environ, self.env, clear=False)
        env_patch.start()
        self.addCleanup(env_patch.stop)

        patches = [
            patch.object(config_ingestao, "INGEST_ABERTO", True),
            patch.object(app, "verificar_turnstile", lambda token, ip: token == "token-ok"),
            patch.object(app, "disparar_worker", lambda job_id: True),
            patch.object(app, "_agora", lambda: datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)),
            patch.object(app, "_duplicata_manifesto", lambda hash_: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def enviar(self, dados=None):
        return app.enviar(FakeRequest(), dados if dados is not None else dados_envio())

    def erro(self, dados=None):
        with self.assertRaises(app.HTTPException) as contexto:
            self.enviar(dados)
        return contexto.exception

    def job(self, status="na_fila", **extra):
        job = {
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-ABCDEF",
            "status": status,
            "motivo": None,
            "etapa": None,
            "posicao_fila": None,
            "pr_url": None,
            "criado_em": "2026-11-08T11:00:00Z",
            "atualizado_em": "2026-11-08T11:05:00Z",
            "conteudo_bruto": "texto privado",
        }
        job.update(extra)
        self.fake.jobs.append(job)
        return job


class TestContratoConfig(ContratoBase):
    def test_config_shape_consumido_pela_pagina(self):
        resposta = app.config_enviar()
        self.assertEqual(
            set(resposta),
            {"aberto", "turnstile_site_key", "max_bytes", "tipos"},
        )
        self.assertIs(resposta["aberto"], True)
        self.assertEqual(resposta["max_bytes"], config_ingestao.MAX_BYTES)
        self.assertEqual(
            resposta["tipos"],
            [
                {"valor": "ata", "rotulo": "Ata"},
                {"valor": "guia", "rotulo": "Guia"},
                {"valor": "outro", "rotulo": "Relato"},
            ],
        )

    def test_config_fechado(self):
        with patch.object(config_ingestao, "INGEST_ABERTO", False):
            self.assertIs(app.config_enviar()["aberto"], False)


class TestContratoEnvio(ContratoBase):
    def test_sucesso_devolve_link_compartilhavel(self):
        resposta = self.enviar()
        self.assertEqual(
            set(resposta),
            {"id", "source_id", "status_url", "posicao_fila"},
        )
        self.assertEqual(resposta["status_url"], f"/enviar/{resposta['id']}")
        self.assertEqual(resposta["posicao_fila"], 1)

    def test_mensagens_por_status(self):
        with patch.object(config_ingestao, "INGEST_ABERTO", False):
            fechado = self.erro({"tipo": "invalido"})
        self.assertEqual((fechado.status_code, fechado.detail), (503, app.MSG_ENVIO_FECHADO))

        with patch.dict(os.environ, {"TURNSTILE_SITE_KEY": ""}):
            sem_chave = self.erro()
        self.assertEqual((sem_chave.status_code, sem_chave.detail), (503, app.MSG_ENVIO_FECHADO))

        pii = self.erro(dados_envio(texto="Fale com pessoa@gmail.com"))
        self.assertEqual((pii.status_code, pii.detail), (422, app.MSG_PII))

        titulo = self.erro(dados_envio(titulo="oi"))
        self.assertEqual(titulo.status_code, 422)
        self.assertIn("título", titulo.detail)

        data = self.erro(dados_envio(data_documento="10/04/2030"))
        self.assertEqual(data.status_code, 422)
        self.assertIn("Data", data.detail)

        with patch.object(app, "verificar_turnstile", lambda token, ip: False):
            verificado = self.erro()
        self.assertEqual((verificado.status_code, verificado.detail), (403, app.MSG_TURNSTILE))

    def test_duplicata(self):
        texto = dados_envio()["texto"]
        self.fake.jobs.append({
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-DUPLIC",
            "hash_conteudo": ingerir.hash_conteudo(texto),
            "status": "publicado",
        })
        duplicata = self.erro(dados_envio(texto=texto))
        self.assertEqual(duplicata.status_code, 409)
        self.assertEqual(duplicata.detail, "Esta fonte já foi registrada como ATA-2030-04-10-DUPLIC.")

    def test_limites_por_ip_e_global(self):
        with patch.object(config_ingestao, "INGEST_MAX_POR_IP", 1):
            self.enviar()
            por_ip = self.erro()
        self.assertEqual(por_ip.status_code, 429)
        self.assertEqual(por_ip.detail, "Limite de 1 envios por dia. Tente novamente amanhã.")

        self.fake = FakeDB()
        with patch.object(app, "db", self.fake):
            self.fake.limites["global:2026-11-08"] = config_ingestao.INGEST_MAX_DIA
            global_ = self.erro()
        self.assertEqual(global_.status_code, 429)
        self.assertEqual(global_.detail, "A fila de envios está cheia hoje. Tente novamente amanhã.")
        self.assertEqual(self.fake.jobs, [])


class TestContratoAndamento(ContratoBase):
    def test_payload_publico_e_mensagem_na_fila(self):
        job = self.job()
        resposta = app.andamento(job["id"], FakeRequest())
        self.assertEqual(
            set(resposta),
            {
                "id", "source_id", "status", "motivo", "etapa", "posicao_fila",
                "pr_url", "mensagem", "criado_em", "atualizado_em",
            },
        )
        self.assertEqual(resposta["posicao_fila"], 1)
        self.assertEqual(resposta["mensagem"], "Na fila. Posição 1. A espera depende da revisão da gestão.")
        self.assertNotIn("texto privado", str(resposta))

    def test_mensagem_de_processando(self):
        job = self.job(status="processando", etapa="compilando")
        resposta = app.andamento(job["id"], FakeRequest())
        self.assertIsNone(resposta["posicao_fila"])
        self.assertEqual(resposta["mensagem"], "Processando: compilando a proposta.")

    def test_404_de_uuid_invalido_nao_consulta(self):
        with self.assertRaises(app.HTTPException) as contexto:
            app.andamento("nao-e-uuid", FakeRequest())
        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(contexto.exception.detail, app.MSG_NAO_DISPONIVEL)
        self.assertEqual(self.fake.buscas, 0)

    def test_404_de_job_inexistente(self):
        with self.assertRaises(app.HTTPException) as contexto:
            app.andamento(str(uuid4()), FakeRequest())
        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(contexto.exception.detail, app.MSG_NAO_DISPONIVEL)


if __name__ == "__main__":
    unittest.main()
