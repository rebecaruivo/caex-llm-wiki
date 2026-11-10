# Operação do ingest

Este documento cobre a automação em volta do worker, da fila e da manutenção
diária. A autoridade continua sendo `docs/especificacao-ingest.md` (§6.4, §7,
§9 e §14).

## Workflows

| Arquivo | Quando roda | O que faz |
|---|---|---|
| `.github/workflows/ingerir.yml` | só `workflow_dispatch`, com `job_id` | valida o UUID, faz checkout de `main` e roda `tools/ingerir.py` |
| `.github/workflows/ingest-pr-fechado.yml` | `pull_request: closed` | reconcilia o job quando um PR `ingest/...` fecha |
| `.github/workflows/tests.yml` | `push` e `pull_request` | roda `unittest` e `tools/lint_wiki.py`, sem secrets |

Regras de segurança verificadas pela suíte:

- nenhum workflow usa `pull_request_target`;
- o worker só roda por `workflow_dispatch` e só faz checkout de `main`;
- o fechamento só age em PR do próprio repositório e de branch `ingest/...`;
- o worker recebe `GEMINI_API_KEY_INGEST`, `SUPABASE_URL`,
  `SUPABASE_SECRET_KEY`, `NOTIFICACOES_WEBHOOK_URL` e
  `NOTIFICACOES_WEBHOOK_SECRET`;
- o fechamento recebe só `SUPABASE_URL` e `SUPABASE_SECRET_KEY` e usa o
  `GITHUB_TOKEN` efêmero do Actions (`actions: write`) para avançar a fila;
- a API da Vercel nunca recebe a chave de ingestão e o worker nunca recebe o
  `GITHUB_DISPATCH_TOKEN` da Vercel.

O `MODELO_INGEST` é constante em `config_ingestao.py`; nenhum workflow lê
variável de repositório. A escolha com evidência ao vivo está em
`docs/modelo-ingest.md`.

## Fechamento do PR

O script `tools/ingest_pr_fechado.py` revalida repositório, branch e número do
PR antes de tocar no banco. Com merge, o job vira `publicado` com
`publicado_em`; sem merge, vira `fechado_sem_merge`. Nos dois casos:

1. o original (`conteudo_bruto`) e `nomes_detectados` são apagados;
2. o resultado terminal é gravado em `ingest_hashes`;
3. o lock é liberado;
4. só então a mesma `talvez_avancar_fila()` do `app.py` é chamada. Ela toma o
   lock com `tomar_lock_fila` e só depois dispara `ingerir.yml`.

O workflow nunca dispara o próximo worker diretamente.

## Manutenção diária

`GET /api/ingest/manutencao` exige `Authorization: Bearer $CRON_SECRET`. A
Vercel Cron chama uma vez por dia às 06:00 (`vercel.json`, expressão
`0 6 * * *`) e envia o cabeçalho sozinha quando `CRON_SECRET` existe.

Também dá para chamar manualmente:

```bash
curl -H "Authorization: Bearer $CRON_SECRET" \
  https://caex-llm-wiki.vercel.app/api/ingest/manutencao
```

Os sete passos são idempotentes:

1. apaga `conteudo_bruto`/`nomes_detectados` de jobs vencidos; `na_fila` e
   `processando` viram `expirado`, `aguardando_revisao` mantém status e lock;
2. apaga linhas vencidas de `limites_ingest`;
3. apaga jobs terminais com mais de 90 dias e seus eventos;
4. marca `processando` parado há mais de 15 minutos como `falhou`/`timeout` e
   libera o lock;
5. libera lock órfão (job terminal ou inexistente) e marca `na_fila` preso há
   mais de `LOCK_DIAS` como `falhou`/`dispatch`;
6. consulta o PR na API pública do GitHub quando `aberto_em` tem mais de 7
   dias: aberto renova `aberto_em` e notifica `lock_antigo`; fechado/mesclado
   reconcilia o job. Se `aberto_em` for nulo e o job estiver
   `aguardando_revisao`, procura um PR aberto pelo branch `ingest/<source_id>`;
   se não achar, marca `falhou`/`infra`;
7. chama `talvez_avancar_fila()` uma única vez, no fim.

Falha de GitHub no passo 6 não interrompe os outros passos: o relatório registra
`github_falhou` e a próxima execução tenta de novo.

## Notificações

`notificacoes.py` usa um único `NOTIFICACOES_WEBHOOK_URL`, com
`X-Webhook-Secret` quando `NOTIFICACOES_WEBHOOK_SECRET` está definido. Em falha,
tenta mais uma vez. A notificação nunca derruba o job.

Eventos: `pr_aberto`, `quarentena`, `falhou` e `lock_antigo`. O payload contém
apenas `evento`, `source_id`, `status`, `motivo`, `pr_url`, `paginas`,
`criado_em` e `atualizado_em`. Nunca inclui UUID, texto bruto ou nomes.

## Recuperação manual

- **Rodar um job:** use *Actions → Ingerir fonte → Run workflow* e informe o UUID.
- **Liberar uma quarentena:** no painel do Supabase, mude o job para
  `status = na_fila`, `liberado_pela_gestao = true`, `motivo = null`,
  `etapa = null`, `tentativas = 0`, `falhas_dispatch = 0`, e dispare o workflow
  pelo UUID.
- **Job `falhou` por lint/schema:** corrija a causa, reenfileire da mesma forma
  e dispare o workflow.
- **PR fechado:** o workflow de fechamento reconcilia sozinho. Se ele não rodar,
  a manutenção diária faz a mesma reconciliação pelo lock antigo.
- **Lock preso:** rode a manutenção manualmente; ela libera lock órfão e
  reconcilia PR aberto/fechado.
- **Reenvio:** depois de `falhou`, `expirado` ou `fechado_sem_merge`, o mesmo
  conteúdo pode ser enviado de novo.

Não existe `despachar_fila.py`; a interface do GitHub é o caminho manual.
