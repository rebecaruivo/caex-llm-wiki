# Oráculo interno com LLM Wiki

> **Todos os dados deste repositório são fictícios.** O Centro Acadêmico de Exemplo (CAEX) e a Universidade Modelo do Vale não existem.

Base de conhecimento para um oráculo que responde dúvidas internas com fontes e admite quando não sabe.

**Estado:** wiki, avaliação automática e API publicadas. Chat e automação ainda serão construídos.

## Resultado atual
Com `gemini-3.5-flash-lite`, a versão atual passou em 35 de 36 respostas, ou 97%, em 3 rodadas dos 12 testes.

| Versão | Mudança | Resultado em 3 rodadas |
|---|---|---|
| A | Exemplos de citação no prompt | 33 de 36 |
| B | Contradições sempre visíveis | 35 de 36 |
| C | Contradições só quando afetam a resposta | 33 de 36 |
| D | Remoção do `id` da página do contexto | 35 de 36 |

A versão C também passou a medir menções desnecessárias à contradição. Por isso, os resultados não são totalmente comparáveis entre todas as versões.

A avaliação combina checagem de citações por código e critérios avaliados por um modelo de linguagem. Ela verifica se cada citação existe, mas ainda não confirma se a fonte comprova a frase.

## API
A API está publicada em [caex-llm-wiki.vercel.app/docs](https://caex-llm-wiki.vercel.app/docs), com uma página para testar as chamadas no navegador.

| Endereço | Método | Função |
|---|---|---|
| `/api/saude` | GET | Confirma que a API está no ar |
| `/api/perguntar` | POST | Responde usando só a wiki e devolve as fontes |
| `/api/enviar/config` | GET | Estado público do formulário: aberto, Turnstile, limite e tipos |
| `/api/enviar` | POST | Recebe uma fonte e cria o job em `na_fila` |
| `/api/ingest/{uuid}` | GET | Andamento público do envio (60 consultas/min por IP) |
| `/api/ingest/manutencao` | GET | Manutenção diária; exige `Authorization: Bearer $CRON_SECRET` |

```bash
curl -X POST https://caex-llm-wiki.vercel.app/api/perguntar \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "Quando será a Semana de Integração?"}'
```

A API e a avaliação usam a mesma função `responder`, em `oraculo.py`. Assim, os testes medem o mesmo código publicado.

**Principal aprendizado:** depois de vários ajustes de prompt, a solução mais eficaz foi tirar do contexto o campo que confundia o modelo.

## O problema
Decisões, processos e contatos costumam ficar espalhados em atas, documentos e na memória de quem já passou pela gestão.

## A solução
Em vez de buscar trechos a cada pergunta, um agente compila as fontes em uma wiki em Markdown. Pessoas revisam o conteúdo, e o oráculo responde a partir dessa base.

```text
Fontes -> Compilador -> Validador -> Revisão humana -> Wiki -> Oráculo -> Usuário
```

## Casos demonstrados
| Caso | Exemplo |
|---|---|
| Atualização | A data da Semana de Integração mudou de 15/04 para 22/04 |
| Contradição | Uma ata cita 10/04, mas a assembleia foi em 10/03 |
| Prática ou regra | Uma votação por contraste não vira regra geral |
| Lacuna | O estatuto e o quórum não estão na base |
| Segurança | Uma sugestão maliciosa fica em quarentena |

## Estrutura
| Caminho | Função |
|---|---|
| `sources/` | Fontes fictícias e manifesto |
| `CLAUDE.md` | Regras do agente mantenedor |
| `prompts/` | Prompts do compilador, do classificador e do oráculo |
| `wiki/` | Conhecimento compilado |
| `evals/` | Testes do oráculo, da reescrita e evals ao vivo do ingest |
| `evals/ingest/` | Fixtures I01–I14 do ingest, com resultado e critérios |
| `config_ingestao.py` | Constantes, switches e regexes da ingestão |
| `ingerir.py` | Núcleo do compilador, triagem, substituição de nomes e publicação |
| `notificacoes.py` | Notificação best-effort para o webhook da gestão |
| `github_ingest.py` | Consulta pública de PR para reconciliar locks antigos |
| `reconciliacao.py` | Fechamento do job no merge/close do PR |
| `tests/` | Suíte determinística com `unittest`, sem rede |
| `tools/lint_wiki.py` | Validador automático |
| `tools/compilar_local.py` | Fatia vertical: fonte → compilador → lint → diff |
| `tools/triar_local.py` | Triagem local: pré-filtro, classificador e nomes |
| `tools/run_evals_ingest.py` | Evals ao vivo do ingest e escolha do modelo |
| `tools/ingerir.py` | Worker (`job_id`) e publicação local: triagem → compilador → fonte derivada → manifesto → lint → PR |
| `tools/ingest_pr_fechado.py` | Reconcilia o job quando um PR `ingest/...` fecha |
| `.github/workflows/` | Worker, fechamento do PR e testes/lint |
| `vercel.json` | Cron diário da manutenção (`0 6 * * *`) |
| `docs/operacao-ingest.md` | Operação, recuperação manual e notificações |

## Como usar
```bash
python3 tools/lint_wiki.py
python3 -m unittest discover -s tests
```

No Windows, use `python tools/lint_wiki.py`. Os testes usam só a biblioteca padrão do Python.

## API de envio
O `POST /api/enviar` segue a ordem da especificação: abertura e chaves
(`503`), tamanho/tipo/título/data/texto (`422`), limite por IP (`429`),
Turnstile (`403`), PII (`422`), duplicata (`409`), limite global (`429`),
criação do job (`201`) e avanço da fila. O limite por IP é consumido mesmo
quando Turnstile, PII ou duplicata recusam; o global só conta jobs que chegam
a ser criados. Duplicatas bloqueantes são `na_fila`, `processando`,
`aguardando_revisao`, `publicado`, `sem_mudancas` e `quarentena` (também no
`ingest_hashes` e no manifesto quando a fonte veio de envio). `falhou`,
`expirado` e `fechado_sem_merge` permitem reenvio; o índice único parcial de
hash resolve a corrida.

`GET /api/enviar/config` devolve `aberto`, `turnstile_site_key`, `max_bytes`
e os tipos com rótulos em português (`Ata`, `Guia`, `Relato`). O
`GET /api/ingest/{uuid}` valida o UUID antes de consultar, limita a 60
consultas por minuto por IP, expõe só campos públicos (`id`, `source_id`,
`status`, `motivo`, `etapa`, `posicao_fila`, `pr_url`, `mensagem`,
`criado_em`, `atualizado_em`) e devolve `404` para job inexistente. O UUID é
a capacidade de acompanhamento e nunca aparece em branch, commit, PR, log ou
notificação.

`talvez_avancar_fila()` pega o job `na_fila` mais antigo, toma o lock de 30
segundos, dispara `ingerir.yml` com `GITHUB_DISPATCH_TOKEN` (ou com o
`GITHUB_TOKEN` efêmero no fechamento do PR) e, em falha, incrementa
`falhas_dispatch`. Depois de `MAX_FALHAS_DISPATCH`, o job vira
`falhou`/`dispatch`, o lock é liberado e a gestão é avisada. Nenhuma mensagem
de erro inclui o token, o UUID ou o texto da fonte.

Crie as tabelas do ingest com `supabase/ingest.sql` (separado de
`supabase/schema.sql`). Todas têm RLS ligada e nenhuma policy pública; só a
chave secreta usada pela API acessa os dados. As RPCs são `tomar_lock_fila`,
`liberar_lock_fila`, `marcar_pr_aberto`, `consumir_limite`, `consumir_ip` e
`consumir_global`.

## Ingestão local (fatia vertical)
O comando compila uma fonte local sobre uma **cópia temporária** do repositório,
aplica as operações aceitas, roda o lint e mostra o diff. O repositório
original não é alterado.

```bash
python3 tools/compilar_local.py \
  --tipo ata --titulo "Ata de 10/04" --data 2030-04-10 \
  --texto "A assembleia aprovou o novo calendário."
```

Também aceita `--fonte arquivo.json` (com `tipo`, `titulo`, `data`, `texto`),
`--texto-arquivo`, `--wiki` (raiz alternativa) e `--modelo`. Use `--help` para
ver tudo. Códigos de saída: `0` sem erros de lint, `1` com erros de lint e `2`
para fonte, modelo ou operação inválida.

Variáveis de ambiente:

| Variável | Padrão | Uso |
|---|---|---|
| `GEMINI_API_KEY_INGEST` | — | Chave do worker (cai para `GEMINI_API_KEY`) |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Modelo do oráculo |
| `INGEST_ABERTO` | `false` | Só `true` abre o formulário |
| `INGEST_MAX_DIA` | `20` | Limite global diário |
| `INGEST_MAX_POR_IP` | `3` | Limite diário por IP |
| `GITHUB_DISPATCH_TOKEN` | — | Token da Vercel para disparar `ingerir.yml` |
| `INGEST_SALT` | — | Sal do HMAC de IP e das consultas de andamento |
| `TURNSTILE_SITE_KEY` | — | Chave pública do Turnstile (obrigatória com `INGEST_ABERTO=true`) |
| `TURNSTILE_SECRET_KEY` | — | Chave secreta do Turnstile (obrigatória com `INGEST_ABERTO=true`) |

Contrato mínimo: o modelo recebe a wiki com linhas numeradas e hash
(`L1 [hash=9f13] ...`) e responde em JSON com `fatos_novos`, `mudancas`,
`contradicoes`, `lacunas` e `operacoes`. As operações aceitas são:

- `adicionar_linha` — insere uma linha citada; com `linha` e `hash`, o hash
  precisa bater com a linha atual.
- `marcar_substituida` — exige autoridade igual ou maior que a maior
  autoridade citada na linha e data estritamente maior que a maior data
  citada. Só acrescenta `(substituída por [fonte: ID] em DATA)` ao fim da
  linha; nunca apaga a linha antiga.
- `atualizar_resumo` — só vale junto de um `marcar_substituida` válido para a
  mesma página na mesma proposta.
- `criar_pagina` — só em `wiki/processos/*.md` (`tipo: processo`) ou
  `wiki/decisoes/*.md` (`tipo: decisao`); `relato` e outros caminhos são
  recusados. O código monta as cinco seções obrigatórias (com
  `## Passo a passo` em processos), deriva `fontes` das citações do corpo e
  grava `confianca: baixa`, `status: rascunho` e validação nula.

Toda linha adicionada entre `## O que as fontes dizem` e `## O que ainda não
sabemos`, incluindo `## Passo a passo`, precisa de citação válida. Fonte
`nao-confiavel` nunca sustenta afirmação, e autoridade menor não altera linha
que cita autoridade maior. Cada contradição vira uma linha `P-xx` em
`wiki/pendencias.md` citando a fonte. O código — nunca o modelo — escreve
`index.md`, `log.md` e `pendencias.md`; `contatos.md` é só da gestão e vira
pendência. Páginas tocadas voltam a `status: rascunho`, com validação nula,
confiança mantida e `atualizado_em = max(atual, data_documento)`.

A proposta é atômica: se qualquer operação for recusada, nada é gravado. Nomes
de pessoas em texto gerado bloqueiam a proposta (comparação sem caixa e por
palavra inteira). O compilador tenta até 3 vezes, devolvendo ao modelo a lista
exata de erros; depois disso falha com motivo `lint`, `schema`, `pii` ou
`compilacao`. A aplicação roda o lint na cópia antes de aceitar a proposta.

## Triagem e quarentena
Antes do compilador, cada fonte passa por uma triagem determinística:

1. **Pré-filtro.** Os regexes de `config_ingestao.PREFILTRO` procuram, no
   título e no texto, frases dirigidas à IA (ignorar instruções, mudar de
   papel, revelar prompt de sistema, jailbreak). Palavras isoladas como
   `ignorar`, `regra` ou `instrução` não disparam. A frase legítima “a
   assembleia decidiu ignorar a regra anterior de votação” passa. Um acerto
   marca `quarentena` sem chamar modelo, sem alterar páginas e sem abrir PR.
2. **Classificador.** Uma única chamada em modo JSON ao
   `prompts/classificador-ingest.md` devolve `injecao`, `sinais` e `nomes`.
   Título e texto são sempre dado, nunca instrução. `nomes` traz só pessoas
   naturais, no span exato; cargos, setores e organizações ficam fora.
3. **Nomes.** Cada span exato é trocado por `[nome removido]` em todas as
   ocorrências, com diferença de maiúsculas: “Rosa” vira `[nome removido]`,
   mas “camiseta rosa” não muda.
4. **Veredito.** Injeção verdadeira sem liberação da gestão marca
   `quarentena`, notifica e encerra. Quarentena não entra no manifesto, não
   altera páginas e não abre PR. Com `liberado_pela_gestao`, o pré-filtro é
   pulado, os nomes continuam sendo usados e o veredito de injeção é ignorado.
5. **Falhas.** Formato inválido ou erro de API marca `falhou` com motivo
   `classificacao`.

As funções reutilizáveis ficam em `ingerir.py`: `prefiltro`, `classificar`,
`substituir_nomes`, `contar_substituicoes` e `triar`. O `triar` recebe os
efeitos colaterais por injeção (`atualizar_status`, `notificar`,
`liberar_lock`), então os testes rodam sem rede. Para uma prévia local:

```bash
python3 tools/triar_local.py \
  --titulo "Ata de 10/04" --texto-arquivo ata.md --derivado-saida derivada.md
```

A saída mostra só resultado, motivo e contagens; nunca imprime nomes nem o
texto bruto da fonte. Códigos de saída: `0` aprovada, `1` quarentena e `2`
falha ou entrada inválida.

Para testar o oráculo em um agente, peça:

```text
Use prompts/oraculo.md e responda só com a wiki: Quando será a Semana de Integração?
```

## Evals ao vivo do ingest
Os evals são **manuais e não rodam em CI**. O runner copia o repositório para
um diretório temporário, roda o classificador e o compilador reais com
`GEMINI_API_KEY_INGEST` e nunca toca em Supabase nem no GitHub. O repositório
original só recebe os relatórios em `evals/resultados/`; a wiki e as fontes da
cópia temporária é que são alteradas.

As fixtures `evals/ingest/I01.json` … `I14.json` cobrem injeção óbvia e sutil,
frase legítima de “ignorar a regra anterior”, nomes (“Rosa” e “camiseta rosa”),
nome em texto gerado, contradição, autoridade menor, substituição por fonte
mais nova, `sem_mudancas`, página nova, relato tentando criar página, muitos
fatos com lint limpo, nome no título e fonte liberada pela gestão. Cada
fixture declara `fonte`, `esperado` e `criterios`.

```bash
# Modelo leve sozinho.
python3 tools/run_evals_ingest.py

# Compara o leve com o forte e registra o mais barato que passa em tudo.
python3 tools/run_evals_ingest.py --comparar \
  --modelo gemini-3.5-flash-lite --modelo-forte gemini-3.5-flash

# Um caso só, sem o modelo avaliador.
python3 tools/run_evals_ingest.py --apenas I04 --sem-juiz
```

A pontuação por código cobre resultado, citações, nomes, lint e páginas
alteradas. O juiz (`--juiz`, um modelo só) avalia apenas os critérios
qualitativos. Use `--fixtures` e `--saida` para trocar as pastas e
`--intervalo` para ajustar a espera entre chamadas. A saída mostra só ids,
resultados e contagens; nunca imprime texto bruto da fonte nem nomes.

Cada rodada grava `evals/resultados/ingest-<timestamp>.json` e `.md` com data e
hora, commit, modelos comparados e resultados por caso. O campo
`modelo_escolhido` (e `modelo_ingest_recomendado`) é o primeiro modelo da
comparação que passa em todos os casos, ou seja, o mais barato. Depois de uma
rodada, registre a escolha em `config_ingestao.MODELO_INGEST` e faça um commit
em português; o relatório é a evidência da decisão. Se nenhum modelo passar em
todos os casos, o campo fica `null` e o `MODELO_INGEST` atual é mantido.

## Publicação e PR
O `tools/ingerir.py` é o caminho completo de publicação, reutilizável pelo
worker (ticket #7). Ele roda a triagem, compila com os guardrails, grava a
fonte derivada, atualiza o manifesto, roda o lint e abre o PR. Git, GitHub,
lint, relógio e modelo são injetáveis; a suíte determinística não usa rede.

```bash
python3 tools/ingerir.py --fonte fonte.json
```

A fonte derivada fica em `sources/atas|guias|relatos/<source_id>.md` e é
idêntica ao original byte a byte, exceto pela troca exata de cada span de nome
por `[nome removido]`. O `source_id` usa o prefixo do tipo, a data do
documento e um sufixo hexadecimal; em colisão com o manifesto ou com jobs já
existentes, o sufixo cresce de 6 para 8 e depois 10 caracteres.

A entrada do manifesto registra `id`, título sanitizado, `tipo`,
`autoridade`, `data`, `arquivo`, `uso: wiki`, `origem: envio`,
`submetido_em` e `hash` (SHA-256 do texto normalizado). Fonte em quarentena
nunca entra no manifesto, não gera derivado e não abre PR.

O PR usa o branch `ingest/<source_id>`, um único commit `Ingest <source_id>` e
o título `Ingest <source_id>: <título sem nomes>`. O corpo traz metadados e
hash, aviso de envio anônimo, fatos, mudanças, contradições, lacunas, resultado
do lint, quantidade de substituições, classificação, checklist da gestão e a
data de exclusão do original (30 dias). O UUID do job, o texto bruto e nomes
de pessoas nunca aparecem em branch, commit, título ou corpo.

O lint roda antes de abrir o PR e, se falhar nas três tentativas do
compilador, o resultado é `falhou`/`lint` sem PR. Quando a proposta não tem
operações nem contradições, o resultado é `sem_mudancas`: nenhum PR é aberto e
o original permanece no Supabase até o TTL. Páginas tocadas voltam a
`status: rascunho`, perdem validação, mantêm `confianca` e recebem
`atualizado_em = max(atual, data_documento)`.

Códigos de saída: `0` publicado ou sem mudanças, `1` quarentena e `2` falha ou
entrada inválida.

## Automação, manutenção e notificações
O worker roda só por `workflow_dispatch` com o UUID do job; o fechamento de PR
só age em branches `ingest/...` do próprio repositório; nenhum workflow usa
`pull_request_target`. O CI roda `unittest` e `tools/lint_wiki.py` em push e
pull request, sem secrets.

A manutenção diária fica em `GET /api/ingest/manutencao`, protegida por
`Authorization: Bearer $CRON_SECRET` e agendada em `vercel.json` às 06:00. Ela
executa sete passos idempotentes: TTL de 30 dias do original e dos nomes,
limpeza de limites, retenção de 90 dias, timeout de 15 minutos, lock órfão,
reconciliação de lock antigo pelo PR público e avanço da fila. Falha de GitHub
fica para a próxima execução.

As notificações usam um único webhook, com `X-Webhook-Secret` quando
configurado, uma tentativa extra e nunca derrubam o job. Os eventos são
`pr_aberto`, `quarentena`, `falhou` e `lock_antigo`; o payload não tem UUID,
texto bruto nem nomes. Detalhes de operação e recuperação manual estão em
[`docs/operacao-ingest.md`](docs/operacao-ingest.md).

## Próximos passos
1. Rodar os testes e publicar a taxa de acerto.
2. Criar a API do oráculo.
3. Publicar o chat.
4. Automatizar a entrada de fontes com n8n.
5. Criar o dashboard de perguntas e lacunas.
