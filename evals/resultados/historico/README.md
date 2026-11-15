# Histórico das rodadas de eval do ingest (11 a 15/11/2026)

> **Reconstituído.** Os relatórios brutos das rodadas intermediárias foram
> apagados por engano antes de entrar no Git. Este resumo foi escrito a partir
> das análises feitas durante a depuração, que leram cada relatório: os erros,
> as respostas do modelo e as operações aplicadas citados aqui são os que
> apareceram nesses relatórios. Os relatórios finais estão na pasta acima:
> `ingest-2026-11-15-155222` (14 casos) e `ingest-2026-11-15-155758`.

**Ponto de partida:** a suíte `tools/run_evals_ingest.py` (14 casos) nunca tinha
rodado com o Gemini real; os testes usavam um modelo falso.
**Ponto de chegada:** 14 de 14 casos aprovados com `gemini-3.5-flash-lite`.

## Fase A — o caso I08 (substituir uma data), rodada a rodada

O I08 pede para reagendar a Semana de Integração de 22/04 para 25/04: marcar a
linha antiga como substituída, atualizar o resumo e registrar o fato novo.

| Quando | Caso | O que o relatório mostrou | Correção |
|---|---|---|---|
| 11/11 20:17 | I10 | `ok`, mas reprovado: o fato foi para a página da assembleia em vez de uma página nova de decisão | Prompt ganha "Onde colocar cada fato"; sai o "Siga `CLAUDE.md`", arquivo que o modelo nunca recebia |
| 11/11 20:20 | I10 | Aprovado | — |
| 11/11 20:24 | I08 | `recusado`, motivo `schema`, 3 tentativas, sem detalhe | O eval passa a guardar a lista de erros |
| 11/11 20:27 | I08 | `marcar_substituida sem 'nova_fonte'` nas 3 tentativas | O código preenche `nova_fonte` com a fonte em compilação, o único valor possível, e recusa outra fonte |
| 11/11 20:30 | I08 | `'linha' precisa ser um inteiro` | Aceita `12`, `"12"` e `"L12"`, ou acha a linha por hash único; o eval passa a guardar a resposta recusada |
| 11/11 20:34 | I08 | A resposta mostra o modelo em loop: `tipo_pagina` e `titulo` dentro de uma `marcar_substituida`, com milhares de caracteres sem sentido | Esquema com um formato por tipo de operação (`anyOf`), campos obrigatórios e `propertyOrdering` |
| 11/11 21:11 | I08 | Resposta correta, mas `hash` `f444` inventado; o real era `fa36` | Hash que não existe em linha nenhuma não bloqueia; hash de outra linha continua bloqueando |
| 11/11 23:29 | I08 | `ok`, `substituicoes: 1`, mas o juiz reprovou "marca a linha antiga" | O juiz não recebia a linha marcada; registro e relatório passam a mostrar as operações aplicadas |
| 12/11 08:38 | I08 | Aprovado na 1ª tentativa: L20 marcada, resumo e fato novo corretos | — |

## Fase B — a suíte completa

| Quando | Resultado | Reprovados e causa | Correção |
|---|---|---|---|
| 12/11 08:53 | 8/14 | I11: relato criou página (bug: a regra comparava o tipo `outro` com a autoridade `relato` e nunca disparava). I04: "camiseta rosa" bloqueada (troca e checagem de nomes com regras de maiúsculas opostas). I06: contradição gravada como fato. I07: quarentena em vez de recusa. I05 e I12: juiz sem evidência (tentativas; linhas gravadas) | Regra de relato por autoridade; regra única de nomes (`padrao_nome`); seção "Substituição ou contradição" no prompt; I07 aceita quarentena; juiz recebe tentativas, erros e linhas gravadas |
| 14/11 09:59 | 10/14 | I04 e I12: expectativas anteriores à regra nova de páginas. I02: juiz sem saber quem barrou a injeção. I06: o modelo gerava as operações antes das contradições | Fixtures atualizadas; `quarentena_por` no resumo do juiz; `propertyOrdering` no nível de cima (fatos → mudanças → contradições → lacunas → operações) |
| 14/11 13:43 | 7 casos sem rodar | `ErroApi` em série: limite de chamadas por minuto do plano gratuito. I06 registrou a pendência, mas o juiz não a via. I07 virou pendência depois da recusa | Compasso de 6,5 s entre chamadas; mensagem do erro no relatório; pendências no resumo do juiz; `paginas_proibidas` no I07 |
| 15/11 11:17 | 10/14 | I14: substituição sem a linha nova (a data nova ficou só no resumo, sem citação). I13: juiz sem o título público. I06 e I09: confirmação e contradição confundidas | Guardrail: toda substituição exige a linha nova citando a fonte, na mesma página; juiz recebe o título público; prompt compara com o valor vigente segundo o `## Resumo` |
| 15/11 15:26 | 7 aprovados, 5 não executados | Cota **diária** do juiz (`gemini-3.5-flash`) esgotada após 9 chamadas | Leitura da cota no erro 429 e `--retomar` (feitos fora desta análise); juiz trocado para `gemini-3.5-flash-lite`, que tem cota própria |
| 15/11 15:52 | 11/14 | I12: posição sugerida errada derrubava a proposta inteira. I11: quarentena não aceita. I07: juiz lia ausência como "não cumprido" | Em `adicionar_linha`, posição divergente vai para o fim da seção; I11 aceita quarentena; resumo diz se o compilador rodou e se alguma página mudou |
| 15/11 15:57 | 3/3 (I07, I11, I12) | — | Com a rodada anterior: 14/14 |

Houve ainda três rodadas sem análise registrada nesta depuração
(12/11 15:30, 14/11 14:17 e 14/11 18:17), provavelmente feitas durante
os ajustes de cota.

## O que este histórico ensina

1. **Observabilidade antes de correção.** Três rodadas do I08 descobriram um
   campo por vez, até o eval guardar a resposta do modelo.
2. **O esquema é parte do prompt.** Formato único com tudo opcional fez o
   modelo misturar campos e entrar em loop; a ordem dos campos virou ordem de
   raciocínio.
3. **Quem errou: modelo, código ou eval?** O juiz reprovou sem evidência em
   sete casos diferentes; o código tinha uma regra de segurança que nunca
   disparava; o modelo errou contradição e substituição.
4. **Uma rodada isolada não basta.** I06 e I09 passaram e falharam em rodadas
   diferentes com o mesmo código; a medida certa é a taxa em várias rodadas.
5. **Cota é por modelo.** O gargalo foi o juiz, não o modelo que gera.
