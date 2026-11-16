# Oráculo do CAEX: um chat que responde só com uma wiki mantida por IA

> **Todos os dados deste repositório são fictícios.** O Centro Acadêmico de Exemplo (CAEX) e a Universidade Modelo do Vale não existem.

Um oráculo para dúvidas internas de um centro acadêmico. Ele responde só com o que está numa wiki revisada por pessoas, cita a fonte de cada informação e diz quando não sabe. A wiki é mantida por um agente: qualquer pessoa envia uma ata pelo site, a IA propõe as mudanças, o código confere e a gestão aprova num Pull Request.

**No ar:** [caex-llm-wiki.vercel.app](https://caex-llm-wiki.vercel.app) · [enviar uma ata](https://caex-llm-wiki.vercel.app/enviar) · [revisão da wiki](https://caex-llm-wiki.vercel.app/revisar) · [documentação da API](https://caex-llm-wiki.vercel.app/docs)

**Estado:** em produção e testado de ponta a ponta. Uma ata enviada pelo site virou um PR, foi aprovada e, minutos depois, o chat respondeu com a informação nova, citando a ata.

## O que ele faz

| Função | Como funciona |
|---|---|
| **Perguntar** | Responde só com a wiki e cita a ata de cada fato. Citações que não existem aparecem riscadas. Quando a wiki não sabe, diz isso e indica quem procurar |
| **Revisar** | A página `/revisar` roda o lint, sem IA: citações, links, estrutura e dados pessoais |
| **Alimentar** | Qualquer pessoa envia uma ata. A IA faz a triagem (injeção de prompt, nomes) e compila as mudanças, o código aplica guardrails e lint, e um PR vai para a gestão |
| Histórico compartilhado | Todo o CA vê e continua os mesmos chats, sem login. Qualquer pessoa pode apagar um chat |
| Perguntas de continuação | "E quem organiza?" vira "Quem organiza a Semana de Integração?" antes de ser respondida |
| Cache exato | A mesma pergunta, com a mesma wiki e o mesmo modelo, reaproveita a resposta. Wiki nova invalida o cache sozinha |

## Arquitetura

```text
                         ┌──────────────── API FastAPI (Vercel) ────────────────┐
Membros do CA ─────────▶ │  Perguntar        Revisar         Enviar             │
                         └──────┬─────────────────────────────────┬─────────────┘
                                │                                 │ fila com trava
                    Gemini + cache (Supabase)                     ▼
                                                     Worker (GitHub Actions)
                                                     triagem → compilador (Gemini)
                                                     → guardrails → lint
                                                                  │
                                                                  ▼
                              Wiki publicada ◀── merge ── Pull Request ◀── Gestão revisa
```

A wiki segue o padrão **LLM Wiki**: em vez de buscar trechos soltos a cada pergunta (RAG), um agente compila as fontes em páginas Markdown, com citação em cada linha, lacunas e contradições explícitas. Atas se substituem e se contradizem; um trecho solto não diz qual vale, uma página compilada diz.

O princípio do ingest: **a IA propõe, o código verifica, uma pessoa aprova.** Os guardrails ficam no código, não no prompt: hierarquia de autoridade das fontes, citação válida em toda linha, nenhum nome de pessoa em texto gerado, toda substituição acompanhada da linha nova, relato não cria página.

## Avaliação

O comportamento é medido com testes, não por impressão.

| Componente | Como é avaliado | Resultado |
|---|---|---|
| Oráculo | 12 perguntas, 3 rodadas, pontuadas por código (citações) e por um modelo juiz (critérios) | 35 de 36 (97%) |
| Reescrita de perguntas | 8 casos com checagem textual, 3 rodadas | 8 de 8 nas 3 rodadas |
| Ingest | 14 casos com o Gemini real: injeção, nomes, contradição, substituição, autoridade, página nova | 14 de 14 ([relatórios](evals/resultados/)) |
| Código | Suíte `unittest` sem rede, no CI a cada PR | Mais de 250 testes |
| Produção | Ata enviada pelo site → PR → merge → resposta citada no chat | Funcionou |

A primeira rodada do ingest com o modelo real deu 8 de 14. O caminho até 14 de 14 está registrado rodada a rodada em [`evals/resultados/historico/`](evals/resultados/historico/README.md).

## Decisões e aprendizados

- **Tirar contexto confuso venceu mais regras no prompt.** O que resolveu as citações erradas do oráculo foi remover do contexto o campo `id` das páginas.
- **O esquema JSON é parte do prompt.** Com um formato único e tudo opcional, o modelo misturava campos de operações diferentes e chegou a entrar em loop. Um formato por operação, com campos obrigatórios, resolveu. A ordem dos campos virou ordem de raciocínio: contradições são geradas antes das operações.
- **Quando um eval reprova, pergunte quem errou.** Nas rodadas do ingest erraram o modelo, o código (uma regra de segurança que nunca disparava) e o próprio eval (um juiz sem evidência). Corrigir o modelo quando o erro é do eval piora o sistema.
- **O que só pode ter um valor, o código preenche.** A fonte que substitui uma linha é sempre a ata em compilação; pedir isso ao modelo só criava mais uma chance de erro.
- **Eval e revisão humana se completam.** Com 14 de 14 no eval, a primeira ata real ainda revelou uma lacuna que nenhum caso cobria. O que a revisão acha vira caso novo.
- **Cache exato, não semântico.** "Quando será a Semana?" e "Quando foi aprovada a Semana?" parecem iguais para uma busca por significado, mas pedem respostas diferentes.
- **Dados pessoais e segurança.** O navegador nunca fala com o banco; só a API tem a chave secreta, e as tabelas têm RLS sem políticas públicas. Texto do modelo entra na página sempre como texto, nunca como HTML. Erros que vão para o log público do Actions são limpos de nomes e PII.

## Processo

As funcionalidades novas seguem um fluxo guiado por especificação, com agentes de IA em papéis separados:

1. **Grill me:** um agente entrevista o plano até cada decisão ter um motivo (47 decisões no ingest).
2. **Spec:** as decisões viram [`docs/especificacao-ingest.md`](docs/especificacao-ingest.md).
3. **Tickets:** a spec vira Issues no GitHub, fatias verticais da mais arriscada para a mais simples.
4. **Implementação:** um ticket por sessão, cada um numa branch, com PR e CI.
5. **Revisão:** uma sessão separada revisa o código gerado; cada achado vira um PR com testes.

Ferramentas: Claude (planejamento, revisão e diagnóstico dos evals), OpenCode e Cursor (implementação), Gemini (o modelo do produto).

## API

| Endereço | Método | Função |
|---|---|---|
| `/api/perguntar` | POST | Responde usando só a wiki. Aceita `chat_id` para continuar uma conversa |
| `/api/chats` | GET | Lista o histórico compartilhado |
| `/api/chats/{id}` | GET / DELETE | Abre ou apaga um chat |
| `/api/wiki` | GET | Páginas, fontes e o que a wiki ainda não cobre |
| `/api/lint` | GET | Revisão automática da wiki |
| `/api/metricas` | GET | Perguntas feitas e respostas reaproveitadas |
| `/api/registros` | POST | Registra uma lacuna ou um erro informado |
| `/api/enviar/config` | GET | Estado público do formulário de envio |
| `/api/enviar` | POST | Recebe uma ata e a coloca na fila |
| `/api/ingest/{id}` | GET | Andamento público de um envio |
| `/api/ingest/manutencao` | GET | Manutenção diária (cron); exige `CRON_SECRET` |
| `/api/saude` | GET | Confirma que a API está no ar |

```bash
curl -X POST https://caex-llm-wiki.vercel.app/api/perguntar \
  -H "Content-Type: application/json" \
  -d '{"pergunta": "Quando será a Semana de Integração?"}'
```

## Estrutura

| Caminho | Função |
|---|---|
| `app.py` | API, páginas, envio e manutenção diária |
| `oraculo.py` | Oráculo, reescrita e chave do cache. API e evals usam o mesmo código |
| `ingerir.py` | Núcleo do ingest: triagem, compilador, guardrails e publicação |
| `tools/ingerir.py` | Worker que roda no GitHub Actions para um envio |
| `fila_ingest.py` | Fila com trava e disparo do worker |
| `db.py` | Acesso ao Supabase pela API REST, sem dependências |
| `prompts/` | Prompts do oráculo, da reescrita, do classificador e do compilador |
| `wiki/`, `sources/` | Conhecimento compilado e fontes |
| `evals/`, `tools/run_evals*.py` | Casos de avaliação, avaliadores e resultados |
| `tests/` | Suíte determinística, sem rede |
| `.github/workflows/` | Worker, fechamento do PR e CI |
| `docs/` | [Especificação](docs/especificacao-ingest.md), [referência técnica](docs/referencia-ingest.md) e [operação](docs/operacao-ingest.md) do ingest |

## Como rodar localmente

1. Crie um projeto no Supabase e rode `supabase/schema.sql` e `supabase/ingest.sql` no SQL Editor.
2. Copie `.env.example` para `.env` e preencha a chave do Gemini, a URL e a chave secreta do Supabase.
3. Instale e rode:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app:app --reload
```

Testes e avaliações:

```bash
python3 -m unittest discover -s tests       # suíte determinística
python3 tools/lint_wiki.py                  # revisão da wiki
python3 tools/testar_banco.py               # conexão com o Supabase
python3 tools/run_evals.py                  # oráculo (usa o Gemini)
python3 tools/run_evals_reescrita.py        # reescrita (usa o Gemini)
python3 tools/run_evals_ingest.py           # ingest (usa o Gemini)
```

O eval do ingest respeita o plano gratuito do Gemini: espaça as chamadas e, se a cota diária acabar, permite continuar depois com `--retomar <relatório>`. `--apenas I07 I11` roda casos específicos e `--juiz <modelo>` troca o modelo avaliador.

## Próximos passos

1. **Fechar lacunas pelo ingest:** a operação `resolver_lacuna` (Issue #13) e os casos I15 e I16, vindos da revisão da primeira ata real.
2. **Eval em várias rodadas:** medir a taxa de aprovação do ingest, porque o mesmo caso pode variar entre rodadas com o modelo leve.
3. **Automação com n8n:** consumir o webhook de notificações do ingest para avisar a gestão e mandar um resumo semanal das lacunas mais perguntadas.
4. **Dashboard** de perguntas, lacunas, reaproveitamento do cache e resultados de eval.
