"""Guardas de segurança dos workflows do GitHub Actions."""
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOWS = RAIZ / ".github/workflows"
INGERIR = WORKFLOWS / "ingerir.yml"
FECHAMENTO = WORKFLOWS / "ingest-pr-fechado.yml"
TESTES = WORKFLOWS / "tests.yml"

SEGREDOS_INGERIR = {
    "GEMINI_API_KEY_INGEST",
    "SUPABASE_URL",
    "SUPABASE_SECRET_KEY",
    "NOTIFICACOES_WEBHOOK_URL",
    "NOTIFICACOES_WEBHOOK_SECRET",
}
GUARDA_REPO = "github.event.pull_request.head.repo.full_name == github.repository"
GUARDA_BRANCH = "startsWith(github.event.pull_request.head.ref, 'ingest/')"


def ler(caminho):
    return caminho.read_text(encoding="utf-8")


class WorkflowsTest(unittest.TestCase):
    def test_nenhum_workflow_usa_pull_request_target(self):
        arquivos = sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(arquivos)
        for arquivo in arquivos:
            with self.subTest(arquivo=arquivo.name):
                self.assertNotIn("pull_request_target", ler(arquivo))

    def test_ingerir_so_roda_por_workflow_dispatch(self):
        texto = ler(INGERIR)
        self.assertIn("workflow_dispatch:", texto)
        self.assertIn("job_id:", texto)
        self.assertNotIn("  pull_request:", texto)
        self.assertNotIn("  push:", texto)

    def test_ingerir_tem_permissoes_concorrencia_e_timeout(self):
        texto = ler(INGERIR)
        self.assertIn("contents: write", texto)
        self.assertIn("pull-requests: write", texto)
        self.assertIn("group: ingest", texto)
        self.assertIn("cancel-in-progress: false", texto)
        self.assertIn("timeout-minutes: 15", texto)
        self.assertIn("ref: main", texto)

    def test_ingerir_usa_exatamente_os_segredos_da_especificacao(self):
        texto = ler(INGERIR)
        usados = set(re.findall(r"secrets\.([A-Z0-9_]+)", texto))
        self.assertEqual(usados, SEGREDOS_INGERIR)

    def test_ingerir_nao_usa_variaveis_de_repositorio(self):
        self.assertNotIn("vars.", ler(INGERIR))

    def test_ingerir_valida_uuid_antes_de_passar_segredos_ao_supabase(self):
        texto = ler(INGERIR)
        posicao_validacao = texto.index("Validar UUID antes do Supabase")
        posicao_worker = texto.index("Rodar worker")
        self.assertLess(posicao_validacao, posicao_worker)
        trecho_validacao = texto[posicao_validacao:posicao_worker]
        self.assertIn("uuid.UUID", trecho_validacao)
        self.assertNotIn("SUPABASE", trecho_validacao)

    def test_fechamento_so_age_em_pr_do_proprio_repo_e_branch_ingest(self):
        texto = ler(FECHAMENTO)
        self.assertIn("pull_request:", texto)
        self.assertIn("types: [closed]", texto)
        self.assertIn(GUARDA_REPO, texto)
        self.assertIn(GUARDA_BRANCH, texto)

    def test_fechamento_tem_permissao_de_actions_e_checkout_main(self):
        texto = ler(FECHAMENTO)
        self.assertIn("contents: read", texto)
        self.assertIn("actions: write", texto)
        self.assertIn("ref: main", texto)
        self.assertIn("python tools/ingest_pr_fechado.py", texto)

    def test_fechamento_nao_dispara_o_worker_diretamente(self):
        texto = ler(FECHAMENTO)
        self.assertNotIn("workflow run", texto)
        self.assertNotIn("ingerir.yml/dispatches", texto)
        self.assertNotIn("disparar_worker", texto)

    def test_testes_rodam_em_push_e_pr_sem_segredos(self):
        texto = ler(TESTES)
        self.assertIn("push:", texto)
        self.assertIn("pull_request:", texto)
        self.assertIn("contents: read", texto)
        self.assertNotIn("secrets.", texto)
        self.assertIn("python -m unittest discover -s tests", texto)
        self.assertIn("python tools/lint_wiki.py", texto)


if __name__ == "__main__":
    unittest.main()
