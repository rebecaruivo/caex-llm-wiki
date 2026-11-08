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
| `prompts/` | Prompts do compilador e do oráculo |
| `wiki/` | Conhecimento compilado |
| `evals/` | 12 testes com critérios de avaliação |
| `config_ingestao.py` | Constantes e switches da ingestão |
| `ingerir.py` | Núcleo do compilador (hash, source_id, contrato JSON) |
| `tests/` | Suíte determinística com `unittest`, sem rede |
| `tools/lint_wiki.py` | Validador automático |
| `tools/compilar_local.py` | Fatia vertical: fonte → compilador → lint → diff |

## Como usar
```bash
python3 tools/lint_wiki.py
```

No Windows, use `python tools/lint_wiki.py`. Os testes usam só a biblioteca padrão do Python.

## Ingestão local (fatia vertical)
O comando compila uma fonte local sobre uma **cópia temporária** do repositório,
aplica apenas operações `adicionar_linha`, roda o lint e mostra o diff. O
repositório original não é alterado.

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

Contrato mínimo: o modelo recebe a wiki com linhas numeradas e hash
(`L1 [hash=9f13] ...`) e responde em JSON com `fatos_novos`, `mudancas`,
`contradicoes`, `lacunas` e `operacoes`. Nesta fatia, cada operação aceita é
`{"tipo": "adicionar_linha", "pagina": "wiki/...", "secao": "...",
"texto": "... [fonte: ID]"}`; qualquer outro tipo é recusado. A citação precisa
existir e apontar para um ID conhecido do manifesto. Se a operação informar
`linha` e `hash`, o hash precisa bater com a linha atual.

Para testar o oráculo em um agente, peça:

```text
Use prompts/oraculo.md e responda só com a wiki: Quando será a Semana de Integração?
```

## Próximos passos
1. Rodar os testes e publicar a taxa de acerto.
2. Criar a API do oráculo.
3. Publicar o chat.
4. Automatizar a entrada de fontes com n8n.
5. Criar o dashboard de perguntas e lacunas.
