# Oráculo interno com LLM Wiki

> **Todos os dados deste repositório são fictícios.** O Centro Acadêmico de Exemplo (CAEX) e a Universidade Modelo do Vale não existem.

Base de conhecimento para um oráculo que responde dúvidas internas com fontes e admite quando não sabe.

**Estado:** wiki, prompts, validador e avaliação automática prontos. API, chat e automação ainda serão construídos.

## Resultado atual
Com `gemini-3.5-flash-lite`, a versão atual passou em 33 de 36 respostas, ou 92%, em 3 rodadas dos 12 testes. A principal falha foi omitir uma contradição relacionada em 2 de 3 rodadas.

A avaliação combina checagem de citações por código e critérios avaliados por um modelo de linguagem. Ela verifica se cada citação existe, mas ainda não confirma se a fonte comprova a frase.

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
| `tools/lint_wiki.py` | Validador automático |

## Como usar
```bash
python3 tools/lint_wiki.py
```

No Windows, use `python tools/lint_wiki.py`. O projeto usa apenas a biblioteca padrão do Python.

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
