# Pedido ao Cursor: interface do Oráculo do CAEX

Use este texto no modo Agent do Cursor. Ele descreve o que construir, o contrato da API e as regras que a interface precisa respeitar.

---

Crie a interface web do Oráculo do CAEX em um único arquivo, `static/index.html`, com HTML, CSS e JavaScript puros. Não use frameworks, bibliotecas de JavaScript nem etapas de build.

## Contexto
O Oráculo responde perguntas sobre um centro acadêmico fictício usando só uma wiki. A API já existe em `app.py`, no mesmo domínio da página. O design de referência está no Figma, na linha "Versão 2.0".

Princípio central: a interface só mostra informação que vem da API. Nada de dados inventados ou fixos que pareçam reais.

## Arquivos que você pode alterar
1. Crie `static/index.html`.
2. Em `app.py`, adicione uma rota `GET /` que devolve esse arquivo, com `include_in_schema=False`. Monte o caminho a partir de `Path(__file__).resolve().parent`, para funcionar localmente e na Vercel.
3. Não altere `oraculo.py`, `prompts/`, `wiki/`, `evals/` nem `tools/`.

## Contrato da API
`GET /api/wiki` devolve:
```json
{
  "atualizado_em": "2030-04-06",
  "paginas": [{"titulo": "Semana de Integração", "tipo": "decisao", "status": "rascunho", "atualizado_em": "2030-04-06", "validado_em": null, "fontes": ["ATA-2030-03-10"]}],
  "ainda_nao_cobre": ["Estatuto do CAEX."],
  "fontes": [{"id": "ATA-2030-03-18", "titulo": "Ata de reunião de 18/03/2030", "tipo": "ata-reuniao", "data": "2030-03-18", "arquivo": "sources/atas/2030-03-18-reuniao.md", "url": "https://github.com/..."}]
}
```

`POST /api/perguntar` recebe `{"pergunta": "texto"}`, com 3 a 500 caracteres, e devolve:
```json
{"resposta": "texto com citações [fonte: ATA-2030-03-18]", "fontes": ["ATA-2030-03-18"], "citacoes_invalidas": []}
```

Erros: 422 para pergunta inválida, 502 quando o modelo falha e 503 quando a chave não está configurada. Em todos, há uma mensagem em `detail`.

## Sistema visual
Fontes do Google Fonts: Newsreader (títulos, pergunta e resposta direta), IBM Plex Sans (interface e texto corrido) e IBM Plex Mono (IDs, datas e números).

Defina estas cores como variáveis CSS em `:root`:
| Variável | Valor | Uso |
|---|---|---|
| `--fundo` | `#F3F0E8` | Fundo da página |
| `--superficie` | `#FBFAF6` | Campo de pergunta |
| `--texto` | `#1C1B18` | Texto principal |
| `--texto-secundario` | `#5F5B52` | Rótulos e texto de apoio |
| `--regra` | `#D8D2C4` | Bordas e divisores |
| `--destaque` | `#1F4D3A` | Botão principal, links e números de citação |
| `--destaque-suave` | `#E2EAE3` | Foco e seleção |
| `--rascunho` | `#835600` | Texto e marcador de rascunho |
| `--rascunho-suave` | `#F2E5C6` | Fundo do aviso de rascunho e da faixa de demo |
| `--sobre-destaque` | `#FFFFFF` | Texto sobre o botão principal |

Regras visuais: raio de 4 a 6 px, sem sombras, sem formatos em pílula e sem gradientes. O verde é a única cor de destaque. O ocre aparece só para "rascunho" e na faixa de demonstração. Nenhum estado depende só da cor: sempre tem um rótulo em texto.

## Layout
- Faixa no topo: "Demonstração com dados fictícios. Nenhuma informação desta tela é real."
- Cabeçalho: "CAEX" em Newsreader e "Oráculo da wiki". À direita, "Wiki atualizada em DD/MM/AAAA" em mono, vindo de `/api/wiki`, e um link "Código no GitHub" para `https://github.com/rebecaruivo/caex-llm-wiki`.
- Corpo: coluna principal de até 680 px e lateral de 300 px. Abaixo de 900 px de largura, a lateral vai para baixo da coluna principal.

## Estados
### 1. Início
- Título: "O que você quer saber sobre o CAEX?"
- Texto: "Cada resposta vem da wiki do Centro Acadêmico, com a ata ou o documento de onde saiu. Se a wiki não tiver a informação, o oráculo diz isso e indica quem procurar."
- Campo de pergunta dentro do conteúdo, com contador `0 / 500`, a dica "Enter envia · Shift + Enter quebra a linha" e o botão "Perguntar".
- "Perguntas de exemplo", em lista com metadado à direita:
  - "Quando será a Semana de Integração?" · "Eventos"
  - "Como crio um grupo de trabalho?" · "Grupos de trabalho"
  - "Qual é o quórum de uma assembleia?" · "Sem registro na wiki"
  Ao clicar, a pergunta é enviada.
- Lateral "O que a wiki cobre": cada página de `/api/wiki` com título e estado. Rascunho aparece em ocre como "Rascunho · aguarda a gestão". Página com `validado_em` aparece em verde como "Revisada em DD/MM/AAAA".
- Lateral "Ainda não cobre": a lista `ainda_nao_cobre`.

### 2. Carregando
- "Você perguntou" e a pergunta em Newsreader.
- Um único estado: "Consultando a wiki…" com o tempo decorrido em segundos, em mono.
- Botão "Cancelar", que interrompe a requisição com `AbortController` e volta ao campo com a pergunta preenchida.
- Não mostre etapas de progresso falsas. A API não informa etapas.
- Lateral "Como o oráculo responde": 1. Procura só na wiki do CAEX. 2. Cita o documento de cada informação. 3. Se não encontra fonte, avisa em vez de inventar.

### 3. Resposta
- "Você perguntou" e a pergunta.
- Converta cada `[fonte: ID]` do texto em um número sobrescrito, na ordem da primeira aparição. O mesmo ID usa sempre o mesmo número. Cada número é um link para a fonte correspondente na lista.
- Remova do texto citações cujo ID não exista em `/api/wiki`, sem numerá-las.
- Remova da resposta linhas de aviso de rascunho escritas pelo modelo, como "Aviso: esta página está em rascunho". A interface mostra esse aviso no formato dela.
- Mostre a primeira frase da resposta em Newsreader, maior. O restante fica em IBM Plex Sans, em parágrafos.
- Aceite apenas esta formatação vinda do texto: parágrafos separados por linha em branco, listas com linhas começando por "- " ou "* " e negrito com `**texto**`.
- Aviso de rascunho: se alguma página de `/api/wiki` com status `rascunho` tiver entre as suas `fontes` um ID citado, mostre "Rascunho" e a frase "A resposta usa uma página ainda não validada pela gestão. A informação pode mudar."
- "Fontes": lista numerada com o ID em mono, o título e a data da fonte. À direita, o link "Abrir documento", que abre a `url` em nova aba.
- Ação "Copiar resposta", copiando o texto sem as marcas de citação.
- Lateral "Páginas ligadas às fontes": páginas de `/api/wiki` que compartilham ao menos uma fonte com a resposta, com título, estado e data. Deixe claro no rótulo que a relação vem das fontes citadas.
- Campo "Faça outra pergunta" abaixo.

### 4. Lacuna
Quando a resposta contiver "Não encontrei isso na wiki":
- Título: "A wiki não tem essa informação."
- Mostre o restante do texto da resposta.
- "Quem pode responder": quando houver um e-mail no texto, mostre-o como link `mailto:`.
- Lateral igual à do início: o que a wiki cobre e o que ainda não cobre.

### 5. Erro
Para 502, 503 ou falha de rede: "Não foi possível consultar o oráculo agora." Mostre o botão "Tentar de novo" e mantenha a pergunta preenchida. Não mostre detalhes técnicos.

### 6. Pergunta inválida
- Com menos de 3 caracteres, não envie e mostre abaixo do campo: "Escreva uma pergunta com pelo menos 3 caracteres."
- Acima de 500 caracteres, deixe o contador em ocre, desative o botão e mostre: "A pergunta passou de 500 caracteres."
- Se a API devolver 422, mostre a mesma orientação.

## Segurança e acessibilidade
- Nunca use `innerHTML` com texto vindo da API. Monte os elementos com `textContent` e `createElement`. A resposta vem de um modelo de linguagem e deve ser tratada como texto não confiável.
- O campo tem um `<label>` visível ou acessível.
- A área de resultado usa `aria-live="polite"`.
- Depois da resposta, mova o foco para o título da resposta.
- Todos os controles funcionam pelo teclado e têm foco visível.
- Botão desativado enquanto uma pergunta está em andamento.

## Como verificar
Ao terminar, liste os arquivos alterados e explique como testar cada estado com `uvicorn app:app --reload`.
