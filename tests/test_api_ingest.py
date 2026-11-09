"""API de envio e andamento do ingest, com Supabase e HTTP falsos."""
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import app
import config_ingestao
import db
import ingerir


class FakeRequest:
    def __init__(self, ip="203.0.113.7", xff=None):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = SimpleNamespace(host=ip)


class FakeDB:
    """Supabase em memória: só os métodos usados pela API de ingestão."""

    ErroBanco = db.ErroBanco
    DuplicataIngest = db.DuplicataIngest
    SourceIdEmUso = db.SourceIdEmUso

    def __init__(self):
        self.jobs = []
        self.hashes = []
        self.limites = {}
        self.eventos = []
        self.lock = None
        self.dispatch_chamado = []
        self.buscas = 0
        self.esconder_duplicata = False
        self.proximo_segundo = 0

    def configurado(self):
        return True

    def _agora_iso(self):
        self.proximo_segundo += 1
        base = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
        return base.replace(second=min(self.proximo_segundo, 59)).isoformat().replace("+00:00", "Z")

    def consumir(self, chave, limite):
        contagem = self.limites.get(chave, 0)
        if contagem >= limite:
            return None
        contagem += 1
        self.limites[chave] = contagem
        return contagem

    def consumir_ip(self, chave, dia, limite, expira_em):
        return self.consumir(chave, limite)

    def consumir_global(self, chave, dia, limite, expira_em):
        return self.consumir(chave, limite)

    def consumir_status(self, chave, dia, limite, expira_em):
        return self.consumir(chave, limite)

    def buscar_job_bloqueante_por_hash(self, hash_, statuss):
        if self.esconder_duplicata:
            self.esconder_duplicata = False
            return None
        for job in self.jobs:
            if job.get("hash_conteudo") == hash_ and job.get("status") in statuss:
                return {"id": job["id"], "source_id": job["source_id"], "status": job["status"]}
        return None

    def buscar_hash_ingest(self, hash_, resultados):
        for item in self.hashes:
            if item["hash_conteudo"] == hash_ and item["resultado"] in resultados:
                return item
        return None

    def listar_source_ids_ingest(self):
        return [job["source_id"] for job in self.jobs]

    def criar_job_ingest(self, dados):
        if any(job["source_id"] == dados["source_id"] for job in self.jobs):
            raise db.SourceIdEmUso("source_id em uso")
        if any(
            job.get("hash_conteudo") == dados["hash_conteudo"]
            and job.get("status") in config_ingestao.STATUS_BLOQUEANTES
            for job in self.jobs
        ):
            raise db.DuplicataIngest("hash bloqueante")
        job = dict(dados)
        job["id"] = str(uuid4())
        job["criado_em"] = self._agora_iso()
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
        return len([
            job for job in self.jobs
            if job.get("status") == "na_fila" and job.get("criado_em", "") < criado_em
        ])

    def job_mais_antigo_na_fila(self):
        fila = [job for job in self.jobs if job.get("status") == "na_fila"]
        return fila[0] if fila else None

    def tomar_lock_fila(self, job_id, intervalo_segundos):
        self.lock = str(job_id)
        return True

    def liberar_lock_fila(self, job_id):
        if self.lock == str(job_id):
            self.lock = None

    def marcar_pr_aberto(self, job_id, pr_numero, pr_url):
        self.lock = str(job_id)

    def atualizar_job_ingest(self, job_id, campos):
        for job in self.jobs:
            if job["id"] == str(job_id):
                job.update(campos)
                return job
        return None

    def registrar_evento_ingest(self, job_id, evento, detalhe=None):
        self.eventos.append({"job_id": str(job_id), "evento": evento, "detalhe": detalhe or {}})


def texto_padrao():
    return "A assembleia aprovou o novo calendario e registrou a decisao."


def dados_envio(**mudancas):
    dados = {
        "tipo": "ata",
        "titulo": "Ata de reuniao de 10/04/2030",
        "data_documento": "2030-04-10",
        "texto": texto_padrao(),
        "turnstile_token": "token-ok",
    }
    dados.update(mudancas)
    return dados


class BaseEnvio(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        self.db_patch = patch.object(app, "db", self.fake)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

        self.env = {
            "TURNSTILE_SITE_KEY": "site-key",
            "TURNSTILE_SECRET_KEY": "secret-key",
            "INGEST_SALT": "sal-de-teste",
            "GITHUB_DISPATCH_TOKEN": "token-de-teste",
        }
        self.env_patch = patch.dict(os.environ, self.env, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

        self.patches = [
            patch.object(config_ingestao, "INGEST_ABERTO", True),
            patch.object(config_ingestao, "INGEST_MAX_DIA", 20),
            patch.object(config_ingestao, "INGEST_MAX_POR_IP", 3),
            patch.object(config_ingestao, "STATUS_RATE_LIMIT_POR_MINUTO", 60),
            patch.object(app, "verificar_turnstile", lambda token, ip: token == "token-ok"),
            patch.object(app, "disparar_worker", lambda job_id: self.fake.dispatch_chamado.append(job_id) or True),
            patch.object(app, "_agora", lambda: datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)),
            patch.object(app, "_duplicata_manifesto", lambda hash_: None),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def enviar(self, dados=None, request=None):
        return app.enviar(request or FakeRequest(), dados if dados is not None else dados_envio())


class TestVerificadorTurnstile(unittest.TestCase):
    def test_sem_token_ou_segredo_falha_sem_rede(self):
        self.assertFalse(app.verificar_turnstile("", "203.0.113.7", secret="segredo"))
        self.assertFalse(app.verificar_turnstile("token", "203.0.113.7", secret=""))


class TestConfig(BaseEnvio):
    def test_config_devolve_rotulos_portugueses(self):
        resposta = app.config_enviar()
        self.assertTrue(resposta["aberto"])
        self.assertEqual(resposta["max_bytes"], 51200)
        self.assertEqual(
            resposta["tipos"],
            [
                {"valor": "ata", "rotulo": "Ata"},
                {"valor": "guia", "rotulo": "Guia"},
                {"valor": "outro", "rotulo": "Relato"},
            ],
        )

    def test_config_devolve_site_key_quando_aberto(self):
        self.assertEqual(app.config_enviar()["turnstile_site_key"], "site-key")

    def test_config_fechado_quando_switch_desligado(self):
        with patch.object(config_ingestao, "INGEST_ABERTO", False):
            self.assertFalse(app.config_enviar()["aberto"])


class TestEnvioValidacao(BaseEnvio):
    def assert_http(self, codigo, funcao, *args, **kwargs):
        with self.assertRaises(app.HTTPException) as erro:
            funcao(*args, **kwargs)
        self.assertEqual(erro.exception.status_code, codigo)
        return erro.exception.detail

    def test_fechado_antes_de_qualquer_validacao(self):
        with patch.object(config_ingestao, "INGEST_ABERTO", False):
            detalhe = self.assert_http(503, self.enviar, {"tipo": "invalido"})
        self.assertEqual(detalhe, app.MSG_ENVIO_FECHADO)
        self.assertEqual(self.fake.limites, {})

    def test_chaves_ausentes_fecham_mesmo_com_switch_ligado(self):
        for chave in ("TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"):
            with self.subTest(chave=chave):
                with patch.dict(os.environ, {chave: ""}):
                    detalhe = self.assert_http(503, self.enviar)
                self.assertEqual(detalhe, app.MSG_ENVIO_FECHADO)

    def test_tamanho_antes_do_tipo(self):
        detalhe = self.assert_http(
            422, self.enviar, {"tipo": "invalido", "texto": "x" * (config_ingestao.MAX_BYTES + 1)}
        )
        self.assertIn("limite", detalhe)

    def test_tipo_invalido(self):
        self.assert_http(422, self.enviar, dados_envio(tipo="pdf"))

    def test_titulo_curto(self):
        detalhe = self.assert_http(422, self.enviar, dados_envio(titulo="oi"))
        self.assertIn("título", detalhe)

    def test_data_invalida(self):
        detalhe = self.assert_http(422, self.enviar, dados_envio(data_documento="10/04/2030"))
        self.assertIn("Data", detalhe)

    def test_texto_vazio(self):
        self.assert_http(422, self.enviar, dados_envio(texto="   "))

    def test_ordem_limite_ip_antes_do_turnstile(self):
        detalhe = self.assert_http(
            403, self.enviar, dados_envio(turnstile_token="token-ruim")
        )
        self.assertEqual(detalhe, app.MSG_TURNSTILE)
        self.assertEqual(sum(self.fake.limites.values()), 1)

    def test_pii_depois_de_consumir_ip(self):
        detalhe = self.assert_http(
            422, self.enviar, dados_envio(texto="Contato: pessoa@gmail.com")
        )
        self.assertEqual(detalhe, app.MSG_PII)
        self.assertEqual(sum(self.fake.limites.values()), 1)

    def test_pii_cpf_ra_telefone_e_email(self):
        casos = [
            "CPF 123.456.789-09",
            "RA 1234567 do aluno",
            "Ligar para (11) 98765-4321",
            "pessoa@gmail.com",
        ]
        for caso in casos:
            with self.subTest(caso=caso):
                self.fake = FakeDB()
                with patch.object(app, "db", self.fake):
                    self.assert_http(422, self.enviar, dados_envio(texto=caso))

    def test_email_institucional_nao_e_pii(self):
        resposta = self.enviar(dados_envio(texto="Contato: secretaria@example.edu"))
        self.assertEqual(resposta["source_id"][:3], "ATA")

    def test_duplicata_depois_de_pii(self):
        texto = texto_padrao()
        self.fake.jobs.append({
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-AAAAAA",
            "hash_conteudo": ingerir.hash_conteudo(texto),
            "status": "publicado",
            "criado_em": "2026-11-08T11:00:00Z",
        })
        detalhe = self.assert_http(409, self.enviar, dados_envio(texto=texto))
        self.assertEqual(detalhe, "Esta fonte já foi registrada como ATA-2030-04-10-AAAAAA.")
        self.assertEqual(sum(self.fake.limites.values()), 1)
        self.assertNotIn("global:", " ".join(self.fake.limites))

    def test_limite_global_so_depois_da_duplicata(self):
        for chave in self.fake.limites:
            self.fail("limite global não deveria ter sido consumido")
        self.fake.limites["global:2026-11-08"] = 20
        detalhe = self.assert_http(429, self.enviar)
        self.assertIn("fila de envios", detalhe)
        self.assertEqual(self.fake.jobs, [])

    def test_limite_por_ip(self):
        with patch.object(config_ingestao, "INGEST_MAX_POR_IP", 1):
            self.enviar()
            detalhe = self.assert_http(429, self.enviar)
        self.assertIn("Limite de 1 envios", detalhe)

    def test_requisicao_na_vercel_exige_x_forwarded_for(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=False):
            detalhe = self.assert_http(400, self.enviar, request=FakeRequest())
        self.assertEqual(detalhe, "Requisição inválida.")


class TestEnvioSucesso(BaseEnvio):
    def test_cria_job_na_fila_e_dispara_worker(self):
        resposta = self.enviar()
        self.assertEqual(resposta["status_url"], f"/enviar/{resposta['id']}")
        self.assertEqual(resposta["posicao_fila"], 1)
        self.assertEqual(len(self.fake.jobs), 1)
        job = self.fake.jobs[0]
        self.assertEqual(job["status"], "na_fila")
        self.assertEqual(job["origem"], "web")
        self.assertEqual(job["conteudo_bruto"], texto_padrao())
        self.assertRegex(job["source_id"], r"^ATA-2030-04-10-[0-9A-F]{6,10}$")
        self.assertEqual(self.fake.dispatch_chamado, [job["id"]])
        self.assertIn("global:2026-11-08", self.fake.limites)

    def test_source_id_colidente_cresce_sufixo(self):
        texto = texto_padrao()
        primeiro = ingerir.gerar_source_id("ata", "2030-04-10", set(), texto, sufixo_sempre=True)
        segundo = ingerir.gerar_source_id("ata", "2030-04-10", {primeiro}, texto, sufixo_sempre=True)
        self.fake.jobs.extend([
            {"id": str(uuid4()), "source_id": primeiro, "hash_conteudo": "a" * 64, "status": "falhou", "criado_em": "2026-11-01T00:00:00Z"},
            {"id": str(uuid4()), "source_id": segundo, "hash_conteudo": "b" * 64, "status": "falhou", "criado_em": "2026-11-01T00:00:01Z"},
        ])
        resposta = self.enviar()
        terceiro = ingerir.gerar_source_id("ata", "2030-04-10", {primeiro, segundo}, texto, sufixo_sempre=True)
        self.assertEqual(resposta["source_id"], terceiro)
        self.assertEqual(len(terceiro.rsplit("-", 1)[-1]), 10)

    def test_corrida_de_hash_devolve_409_com_source_id(self):
        texto = texto_padrao()
        existente = {
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-BBBBBB",
            "hash_conteudo": ingerir.hash_conteudo(texto),
            "status": "na_fila",
            "criado_em": "2026-11-08T11:00:00Z",
        }
        self.fake.jobs.append(existente)
        self.fake.esconder_duplicata = True
        with self.assertRaises(app.HTTPException) as erro:
            self.enviar(dados_envio(texto=texto))
        self.assertEqual(erro.exception.status_code, 409)
        self.assertIn("ATA-2030-04-10-BBBBBB", erro.exception.detail)


class TestDuplicatas(BaseEnvio):
    def _job(self, status):
        self.fake.jobs.append({
            "id": str(uuid4()),
            "source_id": f"ATA-2030-04-10-{status[:3].upper()}123",
            "hash_conteudo": ingerir.hash_conteudo(texto_padrao()),
            "status": status,
            "criado_em": "2026-11-08T11:00:00Z",
        })

    def test_status_bloqueantes_recusam(self):
        for status in config_ingestao.STATUS_BLOQUEANTES:
            with self.subTest(status=status):
                self.fake = FakeDB()
                with patch.object(app, "db", self.fake):
                    self._job(status)
                    with self.assertRaises(app.HTTPException) as erro:
                        self.enviar()
                self.assertEqual(erro.exception.status_code, 409)

    def test_status_reenviaveis_permitem(self):
        for status in config_ingestao.STATUS_REENVIAVEL:
            with self.subTest(status=status):
                self.fake = FakeDB()
                with patch.object(app, "db", self.fake):
                    self._job(status)
                    resposta = self.enviar()
                self.assertEqual(resposta["source_id"][:3], "ATA")
                self.assertEqual(len(self.fake.jobs), 2)

    def test_hash_terminal_bloqueante(self):
        self.fake.hashes.append({
            "hash_conteudo": ingerir.hash_conteudo(texto_padrao()),
            "source_id": "ATA-2030-04-10-CCCCCC",
            "resultado": "publicado",
        })
        with self.assertRaises(app.HTTPException) as erro:
            self.enviar()
        self.assertEqual(erro.exception.status_code, 409)
        self.assertIn("CCCCCC", erro.exception.detail)

    def test_hash_terminal_reenviavel_nao_bloqueia(self):
        self.fake.hashes.append({
            "hash_conteudo": ingerir.hash_conteudo(texto_padrao()),
            "source_id": "ATA-2030-04-10-DDDDDD",
            "resultado": "falhou",
        })
        self.assertEqual(self.enviar()["source_id"][:3], "ATA")


class TestAndamento(BaseEnvio):
    def _job(self, status="na_fila", **extra):
        job = {
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-EEEEEE",
            "status": status,
            "motivo": None,
            "etapa": None,
            "pr_url": None,
            "criado_em": "2026-11-08T11:00:00Z",
            "atualizado_em": "2026-11-08T11:05:00Z",
            "conteudo_bruto": "texto secreto",
            "hash_conteudo": "c" * 64,
            "titulo": "Titulo privado",
        }
        job.update(extra)
        self.fake.jobs.append(job)
        return job

    def test_payload_publico_na_fila(self):
        job = self._job()
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
        self.assertNotIn("conteudo_bruto", resposta)
        self.assertNotIn("texto secreto", json.dumps(resposta))

    def test_posicao_so_existe_na_fila(self):
        job = self._job(status="processando", etapa="compilando")
        resposta = app.andamento(job["id"], FakeRequest())
        self.assertIsNone(resposta["posicao_fila"])
        self.assertEqual(resposta["mensagem"], "Processando: compilando a proposta.")

    def test_mensagem_de_falha_por_pii(self):
        job = self._job(status="falhou", motivo="pii")
        self.assertEqual(
            app.andamento(job["id"], FakeRequest())["mensagem"],
            "Remova nomes de pessoas e reenvie.",
        )

    def test_mensagens_dos_estados(self):
        esperado = {
            "aguardando_revisao": "Proposta enviada para revisão da gestão.",
            "publicado": "Publicado após revisão da gestão.",
            "fechado_sem_merge": "A gestão encerrou a revisão sem publicar.",
            "sem_mudancas": "Nenhum fato novo encontrado. Nada foi publicado.",
            "quarentena": "A fonte foi enviada para revisão da gestão. Nenhuma página foi alterada.",
            "falhou": "Não foi possível concluir. A gestão foi avisada.",
            "expirado": "Este envio expirou. Envie a fonte novamente.",
        }
        for status, mensagem in esperado.items():
            with self.subTest(status=status):
                self.fake = FakeDB()
                with patch.object(app, "db", self.fake):
                    job = self._job(status=status)
                    self.assertEqual(app.andamento(job["id"], FakeRequest())["mensagem"], mensagem)

    def test_uuid_invalido_nao_consulta(self):
        with self.assertRaises(app.HTTPException) as erro:
            app.andamento("nao-e-uuid", FakeRequest())
        self.assertEqual(erro.exception.status_code, 404)
        self.assertEqual(self.fake.buscas, 0)

    def test_job_inexistente_da_404(self):
        with self.assertRaises(app.HTTPException) as erro:
            app.andamento(str(uuid4()), FakeRequest())
        self.assertEqual(erro.exception.status_code, 404)
        self.assertEqual(erro.exception.detail, app.MSG_NAO_DISPONIVEL)

    def test_limite_de_status(self):
        job = self._job()
        with patch.object(config_ingestao, "STATUS_RATE_LIMIT_POR_MINUTO", 1):
            app.andamento(job["id"], FakeRequest())
            with self.assertRaises(app.HTTPException) as erro:
                app.andamento(job["id"], FakeRequest())
        self.assertEqual(erro.exception.status_code, 429)


class TestFila(BaseEnvio):
    def test_sem_token_de_dispatch_conta_falha_sem_vazar_segredo(self):
        job = {
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-FFFFFF",
            "status": "na_fila",
            "falhas_dispatch": 0,
            "criado_em": "2026-11-08T11:00:00Z",
        }
        self.fake.jobs.append(job)

        def falhar(_job_id):
            raise RuntimeError("segredo-nao-pode-vazar")

        for _ in range(config_ingestao.MAX_FALHAS_DISPATCH):
            resultado = app.talvez_avancar_fila(disparar=falhar)
            self.assertNotIn("segredo-nao-pode-vazar", repr(resultado))
            self.assertEqual(self.fake.jobs[0]["status"], "na_fila" if len(self.fake.eventos) < config_ingestao.MAX_FALHAS_DISPATCH else "falhou")

        self.assertEqual(self.fake.jobs[0]["status"], "falhou")
        self.assertEqual(self.fake.jobs[0]["motivo"], "dispatch")
        self.assertEqual(self.fake.jobs[0]["falhas_dispatch"], config_ingestao.MAX_FALHAS_DISPATCH)
        self.assertIsNone(self.fake.lock)

    def test_dispatch_ok_registra_evento(self):
        job = {
            "id": str(uuid4()),
            "source_id": "ATA-2030-04-10-999999",
            "status": "na_fila",
            "falhas_dispatch": 0,
            "criado_em": "2026-11-08T11:00:00Z",
        }
        self.fake.jobs.append(job)
        resultado = app.talvez_avancar_fila(disparar=lambda _id: True)
        self.assertTrue(resultado["disparado"])
        self.assertEqual(self.fake.eventos[-1]["evento"], "dispatch")

    def test_fila_vazia(self):
        self.assertEqual(app.talvez_avancar_fila(), {"disparado": False, "motivo": "fila_vazia"})


class FakeResposta:
    def __init__(self, texto):
        self._texto = texto
        self.headers = {}

    def read(self):
        return self._texto.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class TestRpcBanco(unittest.TestCase):
    def test_consumir_ip_chama_a_rota_rpc(self):
        pedidos = []

        def abrir(pedido, timeout=10):
            pedidos.append(pedido)
            return FakeResposta("3")

        with patch.dict(os.environ, {"SUPABASE_URL": "https://projeto.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret"}, clear=False):
            with patch.object(db, "_abrir", abrir):
                self.assertEqual(db.consumir_ip("ip:abc:2030-04-10", "2030-04-10", 3, "2030-04-11T00:00:00Z"), 3)
        self.assertEqual(len(pedidos), 1)
        self.assertTrue(pedidos[0].full_url.endswith("/rest/v1/rpc/consumir_ip"))
        corpo = json.loads(pedidos[0].data.decode("utf-8"))
        self.assertEqual(corpo["p_chave"], "ip:abc:2030-04-10")
        self.assertEqual(corpo["p_limite"], 3)

    def test_rpc_que_nao_devolve_linha_e_limite_atingido(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "https://projeto.supabase.co", "SUPABASE_SECRET_KEY": "sb_secret"}, clear=False):
            with patch.object(db, "_abrir", lambda pedido, timeout=10: FakeResposta("null")):
                self.assertIsNone(db.consumir_global("global:2030-04-10", "2030-04-10", 20, "2030-04-11T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
