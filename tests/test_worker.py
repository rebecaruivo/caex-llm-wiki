"""Testes do worker de ingestão, com banco, modelo, Git e GitHub falsos."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from uuid import uuid4

from tests.support import criar_repo
from tools import ingerir as worker


AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class FakeDB:
    def __init__(self):
        self.jobs = []
        self.estado = {"id": 1, "job_id": None, "pr_numero": None, "pr_url": None, "aberto_em": None}
        self.hashes = {}
        self.eventos = []
        self.buscas = 0
        self.pr_aberto = []
        self.duplicata_proxima = []

    def configurado(self):
        return True

    def buscar_job_ingest(self, job_id):
        self.buscas += 1
        for job in self.jobs:
            if job["id"] == str(job_id):
                return job
        return None

    def estado_fila(self):
        return dict(self.estado)

    def atualizar_job_ingest(self, job_id, campos):
        for job in self.jobs:
            if job["id"] == str(job_id):
                job.update(campos)
                return job
        return None

    def registrar_evento_ingest(self, job_id, evento, detalhe=None):
        self.eventos.append({"job_id": str(job_id), "evento": evento, "detalhe": detalhe or {}})

    def guardar_hash_ingest(self, hash_, source_id, resultado):
        self.hashes[hash_] = {"hash_conteudo": hash_, "source_id": source_id, "resultado": resultado}

    def liberar_lock_fila(self, job_id):
        if str(self.estado.get("job_id")) == str(job_id):
            self.estado.update({"job_id": None, "aberto_em": None, "pr_numero": None, "pr_url": None})

    def marcar_pr_aberto(self, job_id, pr_numero, pr_url):
        self.pr_aberto.append({"job_id": str(job_id), "pr_numero": pr_numero, "pr_url": pr_url})

    def listar_source_ids_ingest(self):
        return [job["source_id"] for job in self.jobs]

    def buscar_jobs_duplicata_proxima(self, tipo, data_documento, titulo, hash_):
        return list(self.duplicata_proxima)


def criar_job(**campos):
    base = {
        "id": str(uuid4()),
        "source_id": "ATA-2030-04-10-ABCDEF",
        "status": "na_fila",
        "motivo": None,
        "etapa": None,
        "tipo": "ata",
        "titulo": "Ata de reunião de 10/04/2030",
        "data_documento": "2030-04-10",
        "conteudo_bruto": "A assembleia aprovou o novo calendário.",
        "hash_conteudo": "a" * 64,
        "nomes_detectados": None,
        "sinais_injecao": None,
        "liberado_pela_gestao": False,
        "criado_em": "2026-11-08T11:00:00Z",
        "atualizado_em": "2026-11-08T11:00:00Z",
    }
    base.update(campos)
    return base


def classificador_limpo(prompt):
    return json.dumps({"injecao": False, "sinais": [], "nomes": []})


def classificador_com_injecao(prompt):
    return json.dumps({"injecao": True, "sinais": ["instrução"], "nomes": []})


def compilador_adiciona(prompt):
    return json.dumps(
        {
            "fatos_novos": [{"texto": "Novo fato.", "fonte": "ATA-2030-04-10-ABCDEF"}],
            "mudancas": [],
            "contradicoes": [],
            "lacunas": [],
            "operacoes": [
                {
                    "tipo": "adicionar_linha",
                    "pagina": "wiki/processos/exemplo.md",
                    "secao": "O que as fontes dizem",
                    "texto": "Novo fato. [fonte: ATA-2030-04-10-ABCDEF]",
                }
            ],
        }
    )


def compilador_sem_mudancas(prompt):
    return json.dumps(
        {"fatos_novos": [], "mudancas": [], "contradicoes": [], "lacunas": [], "operacoes": []}
    )


class BaseWorker(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDB()
        self.payloads = []

    def executar(self, job_id, **kwargs):
        kwargs.setdefault("db_mod", self.fake)
        kwargs.setdefault("notificar", self.payloads.append)
        kwargs.setdefault("agora", lambda: AGORA)
        kwargs.setdefault("chamar_classificador", classificador_limpo)
        kwargs.setdefault("chamar_compilador", compilador_adiciona)
        return worker.executar_job(job_id, **kwargs)


class ValidacaoWorkerTest(BaseWorker):
    def test_uuid_invalido_encerra_antes_de_consultar_o_banco(self):
        resultado = self.executar("nao-e-uuid")
        self.assertEqual(resultado["motivo"], "uuid_invalido")
        self.assertEqual(self.fake.buscas, 0)

    def test_status_diferente_de_na_fila_nao_tem_efeito(self):
        job = criar_job(status="processando")
        self.fake.jobs = [job]
        resultado = self.executar(job["id"])
        self.assertEqual(resultado["motivo"], "status")
        self.assertEqual(self.fake.eventos, [])

    def test_lock_de_outro_job_encerra(self):
        job = criar_job()
        self.fake.jobs = [job]
        self.fake.estado.update({"job_id": str(uuid4())})
        resultado = self.executar(job["id"])
        self.assertEqual(resultado["motivo"], "lock")
        self.assertEqual(job["status"], "na_fila")

    def test_job_inexistente_encerra(self):
        resultado = self.executar(str(uuid4()))
        self.assertEqual(resultado["motivo"], "job_ausente")


class TransicoesWorkerTest(BaseWorker):
    def test_original_nulo_marca_expirado_e_libera_lock(self):
        job = criar_job(conteudo_bruto=None)
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]
        resultado = self.executar(job["id"])
        self.assertEqual(resultado["status"], "expirado")
        self.assertEqual(job["status"], "expirado")
        self.assertIn(job["hash_conteudo"], self.fake.hashes)
        self.assertIsNone(self.fake.estado["job_id"])

    def test_quarentena_nao_chama_compilador_e_libera_lock(self):
        chamadas = []

        def compilador(prompt):
            chamadas.append(prompt)
            return compilador_adiciona(prompt)

        job = criar_job()
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]

        resultado = self.executar(
            job["id"], chamar_classificador=classificador_com_injecao, chamar_compilador=compilador
        )

        self.assertEqual(resultado["status"], "quarentena")
        self.assertEqual(chamadas, [])
        self.assertEqual(job["status"], "quarentena")
        self.assertIsNone(job["motivo"])
        self.assertIn(job["hash_conteudo"], self.fake.hashes)
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.payloads[-1]["evento"], "quarentena")
        self.assertNotIn(job["id"], json.dumps(self.payloads))

    def test_pr_aberto_marca_aguardando_revisao_e_guarda_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            job = criar_job()
            self.fake.jobs = [job]
            self.fake.estado["job_id"] = job["id"]
            git = lambda argumentos, raiz: ""
            gh = lambda argumentos, raiz: "https://github.com/exemplo/repo/pull/12\n"

            resultado = self.executar(job["id"], raiz=raiz, git=git, gh=gh)

            self.assertEqual(resultado["status"], "aguardando_revisao")
            self.assertEqual(job["status"], "aguardando_revisao")
            self.assertEqual(job["pr_numero"], 12)
            self.assertEqual(job["pr_url"], "https://github.com/exemplo/repo/pull/12")
            self.assertEqual(job["branch"], f"ingest/{job['source_id']}")
            self.assertEqual(self.fake.estado["job_id"], job["id"])
            self.assertEqual(self.fake.pr_aberto[0]["pr_numero"], 12)
            self.assertEqual(self.payloads[-1]["evento"], "pr_aberto")
            self.assertNotIn(job["id"], json.dumps(self.payloads))

    def test_duplicata_proxima_marca_label_no_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            job = criar_job()
            self.fake.jobs = [job]
            self.fake.estado["job_id"] = job["id"]
            self.fake.duplicata_proxima = [{
                "id": str(uuid4()),
                "source_id": "ATA-2030-04-10-OUTRA1",
                "tipo": job["tipo"],
                "data_documento": job["data_documento"],
                "titulo": job["titulo"],
                "hash_conteudo": "b" * 64,
            }]
            git = lambda argumentos, raiz: ""
            gh = lambda argumentos, raiz: "https://github.com/exemplo/repo/pull/12\n"

            resultado = self.executar(job["id"], raiz=raiz, git=git, gh=gh)

            self.assertIn("possivel-duplicata", resultado["pr"]["labels"])

    def test_sem_mudancas_libera_lock_sem_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            job = criar_job()
            self.fake.jobs = [job]
            self.fake.estado["job_id"] = job["id"]

            resultado = self.executar(
                job["id"], raiz=raiz, chamar_compilador=compilador_sem_mudancas
            )

            self.assertEqual(resultado["status"], "sem_mudancas")
            self.assertEqual(job["status"], "sem_mudancas")
            self.assertIsNone(job.get("pr_url"))
            self.assertIsNone(self.fake.estado["job_id"])
            self.assertEqual(self.payloads, [])

    def test_falha_de_compilacao_libera_lock_e_notifica(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = criar_repo(tmp)
            job = criar_job()
            self.fake.jobs = [job]
            self.fake.estado["job_id"] = job["id"]

            resultado = self.executar(
                job["id"], raiz=raiz, chamar_compilador=lambda prompt: "não é json"
            )

            self.assertEqual(resultado["status"], "falhou")
            self.assertEqual(job["status"], "falhou")
            self.assertEqual(job["motivo"], "schema")
            self.assertIsNone(self.fake.estado["job_id"])
            self.assertEqual(self.payloads[-1]["evento"], "falhou")
            self.assertNotIn("não é json", json.dumps(self.payloads))

    def test_pii_no_original_marca_falhou_pii_e_libera(self):
        job = criar_job(conteudo_bruto="Contato: pessoa@gmail.com")
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]
        resultado = self.executar(job["id"])
        self.assertEqual(resultado["status"], "falhou")
        self.assertEqual(job["motivo"], "pii")
        self.assertIsNone(self.fake.estado["job_id"])
        self.assertEqual(self.payloads[-1]["evento"], "falhou")

    def test_evento_de_processando_antes_da_triagem(self):
        job = criar_job()
        self.fake.jobs = [job]
        self.fake.estado["job_id"] = job["id"]
        self.executar(job["id"], chamar_classificador=classificador_com_injecao)
        self.assertEqual(self.fake.eventos[0]["evento"], "processando")
        self.assertEqual(self.fake.eventos[0]["detalhe"], {"etapa": "triagem"})


if __name__ == "__main__":
    unittest.main()
