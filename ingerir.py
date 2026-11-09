"""Núcleo puro do compilador e da triagem de ingestão.

Aqui ficam normalização, hash, `source_id`, PII, contexto numerado, contrato
JSON, aplicação de operações, a triagem (pré-filtro, classificador, nomes e
quarentena) e a publicação (fonte derivada, manifesto, lint e PR). Os efeitos
externos são injetáveis para os testes rodarem sem rede.
"""
import hashlib
import json
import os
import re
import subprocess
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from config_ingestao import (  # noqa: F401  (re-exportado para consumidores)
    AUTORIDADE_POR_TIPO,
    DOMINIO_INSTITUCIONAL,
    MAX_BYTES,
    MAX_TENTATIVAS,
    MODELO_INGEST,
    PASTA_POR_TIPO,
    PII_REGEX,
    PREFILTRO,
    PREFIXO_POR_TIPO,
    RETENCAO_BRUTO_DIAS,
    TIPOS,
)

ROOT = Path(__file__).resolve().parent
BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
CITACAO = re.compile(r"\[fonte: ([A-Z0-9-]+)\]")
FONTES_VALIDAS_PLACEHOLDER = "{{fontes_validas}}"

# Hierarquia de autoridade da especificação §7.4.
AUTORIDADE = {
    "oficial": 4,
    "ca-deliberativa": 3,
    "ca-operacional": 2,
    "relato": 1,
}

SECOES_OBRIGATORIAS = (
    "Resumo",
    "O que as fontes dizem",
    "O que ainda não sabemos",
    "Quem procurar",
    "Páginas relacionadas",
)
SECOES_COM_CITACAO = ("O que as fontes dizem", "Passo a passo")
PASTAS_POR_TIPO_PAGINA = {"processos": "processo", "decisoes": "decisao"}
ARQUIVOS_DO_CODIGO = ("wiki/index.md", "wiki/log.md", "wiki/pendencias.md")
ARQUIVO_CONTATOS = "wiki/contatos.md"
TIPOS_OPERACAO = (
    "adicionar_linha",
    "marcar_substituida",
    "atualizar_resumo",
    "criar_pagina",
)


class ErroApi(Exception):
    """Falha ao falar com o Gemini."""


class ErroFonte(Exception):
    """Fonte local inválida (tipo, data, tamanho, PII)."""


class ErroResposta(Exception):
    """Resposta do modelo fora do contrato JSON."""


class ErroOperacao(Exception):
    """Operação recusada pelo código antes de tocar nos arquivos."""


class ErroPii(ErroOperacao):
    """Nome ou dado pessoal em texto gerado."""


class ErroLint(ErroOperacao):
    """Lint da wiki recusou a proposta; guarda a lista exata de erros."""

    def __init__(self, erros):
        self.erros = list(erros)
        super().__init__("lint recusou a proposta: " + "; ".join(self.erros))


class ErroCompilacao(ErroOperacao):
    """Compilação falhou depois de todas as tentativas."""

    def __init__(self, motivo, erros, tentativas):
        self.motivo = motivo
        self.erros = list(erros)
        self.tentativas = tentativas
        super().__init__(
            f"compilação falhou após {tentativas} tentativas "
            f"(motivo: {motivo}): " + "; ".join(self.erros)
        )


class ErroPublicacao(ErroOperacao):
    """Falha ao preparar arquivos ou falar com git/GitHub."""



# Contrato JSON pedido ao Gemini em modo estruturado.
ESQUEMA_RESPOSTA = {
    "type": "OBJECT",
    "properties": {
        "fatos_novos": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"texto": {"type": "STRING"}, "fonte": {"type": "STRING"}},
                "required": ["texto", "fonte"],
            },
        },
        "mudancas": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"texto": {"type": "STRING"}, "fonte": {"type": "STRING"}},
                "required": ["texto", "fonte"],
            },
        },
        "contradicoes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "pagina": {"type": "STRING"},
                    "linha": {"type": "INTEGER"},
                    "resumo": {"type": "STRING"},
                    "fontes": {"type": "ARRAY", "items": {"type": "STRING"}},
                },
                "required": ["pagina", "resumo", "fontes"],
            },
        },
        "lacunas": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"texto": {"type": "STRING"}},
                "required": ["texto"],
            },
        },
        "operacoes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "tipo": {"type": "STRING"},
                    "pagina": {"type": "STRING"},
                    "secao": {"type": "STRING"},
                    "texto": {"type": "STRING"},
                    "linha": {"type": "INTEGER"},
                    "hash": {"type": "STRING"},
                },
                "required": ["tipo"],
            },
        },
    },
    "required": ["operacoes"],
}

# Contrato JSON do classificador de triagem (injecao, sinais, nomes).
ESQUEMA_CLASSIFICADOR = {
    "type": "OBJECT",
    "properties": {
        "injecao": {"type": "BOOLEAN"},
        "sinais": {"type": "ARRAY", "items": {"type": "STRING"}},
        "nomes": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["injecao", "sinais", "nomes"],
}


def normalizar(texto):
    """Normaliza para comparação: strip, espaços colapsados, minúsculas e NFC."""
    texto = re.sub(r"\s+", " ", texto.strip()).lower()
    return unicodedata.normalize("NFC", texto)


def hash_conteudo(texto):
    """SHA-256 do texto normalizado, usado para duplicatas."""
    return hashlib.sha256(normalizar(texto).encode("utf-8")).hexdigest()


def hash_linha(linha):
    """Hash curto (4 hexadecimais) de uma linha exata da wiki."""
    return hashlib.sha256(linha.encode("utf-8")).hexdigest()[:4]


def gerar_source_id(tipo, data, existentes, texto="", sufixo_sempre=False):
    """Gera `PREFIXO-DATA`, acrescentando sufixo hex 6→8→10 em colisão.

    A publicação usa `sufixo_sempre=True` (formato `PREFIXO-DATA-ABCDEF`); o
    parâmetro padrão preserva o comportamento anterior, sem sufixo quando a
    base está livre.
    """
    if tipo not in PREFIXO_POR_TIPO:
        raise ErroFonte(f"tipo inválido para source_id: {tipo!r}")
    base = f"{PREFIXO_POR_TIPO[tipo]}-{data}"
    if not sufixo_sempre and base not in existentes:
        return base
    resumo = hashlib.sha256(normalizar(texto).encode("utf-8")).hexdigest().upper()
    for tamanho in (6, 8, 10):
        candidato = f"{base}-{resumo[:tamanho]}"
        if candidato not in existentes:
            return candidato
    raise ErroFonte(f"não foi possível gerar source_id único para {base}")


def detectar_pii(texto):
    """Devolve rótulos de PII óbvia encontrados no texto, em ordem estável."""
    achados = []
    for rotulo, padrao in PII_REGEX.items():
        if rotulo == "email":
            for email in re.findall(padrao, texto):
                if not email.lower().endswith(DOMINIO_INSTITUCIONAL):
                    achados.append(rotulo)
                    break
        elif re.search(padrao, texto, re.IGNORECASE if rotulo == "ra" else 0):
            achados.append(rotulo)
    return achados


def prefiltro(texto, titulo=""):
    """Indica se título + texto contêm alguma frase de injeção dirigida à IA.

    É determinístico e não chama modelo. Os padrões estão em
    `config_ingestao.PREFILTRO` e só casam instruções, nunca palavras
    isoladas como `ignorar`, `regra` ou `instrução`.
    """
    alvo = f"{titulo}\n{texto}"
    return any(re.search(padrao, alvo, re.IGNORECASE) for padrao in PREFILTRO)


def validar_fonte(fonte):
    """Valida e devolve a fonte com campos texto normalizados."""
    if not isinstance(fonte, dict):
        raise ErroFonte("a fonte precisa ser um objeto JSON")
    tipo = fonte.get("tipo")
    if tipo not in TIPOS:
        raise ErroFonte(f"tipo inválido: {tipo!r}; use um de {', '.join(TIPOS)}")
    titulo = str(fonte.get("titulo", "")).strip()
    if not titulo:
        raise ErroFonte("título vazio")
    data = str(fonte.get("data", "")).strip()
    try:
        date.fromisoformat(data)
    except ValueError as erro:
        raise ErroFonte(f"data inválida: {data!r}; use AAAA-MM-DD") from erro
    texto = fonte.get("texto")
    if not isinstance(texto, str) or not texto.strip():
        raise ErroFonte("texto vazio")
    if len(texto.encode("utf-8")) > MAX_BYTES:
        raise ErroFonte(f"texto acima do limite de {MAX_BYTES} bytes")
    pii = detectar_pii(texto)
    if pii:
        raise ErroFonte(f"PII óbvia detectada: {', '.join(pii)}")
    return {"tipo": tipo, "titulo": titulo, "data": data, "texto": texto}


def carregar_manifesto(raiz):
    """Lê o manifesto de fontes da raiz informada."""
    return json.loads((Path(raiz) / "sources/manifest.json").read_text(encoding="utf-8"))


def ids_manifesto(raiz):
    """Mapa id → registro de todas as fontes do manifesto."""
    return {fonte["id"]: fonte for fonte in carregar_manifesto(raiz)["fontes"]}


def fontes_validas_para_compilar(raiz, source_id):
    """IDs que podem sustentar afirmações, mais o novo `source_id`."""
    validos = {
        fonte_id
        for fonte_id, fonte in ids_manifesto(raiz).items()
        if fonte.get("uso") != "quarentena" and fonte.get("autoridade") != "nao-confiavel"
    }
    validos.add(source_id)
    return validos


def montar_contexto_numerado(raiz):
    """Monta a wiki como blocos `<pagina>` com linhas numeradas e hash."""
    raiz = Path(raiz)
    partes = []
    for pagina in sorted((raiz / "wiki").rglob("*.md")):
        relativo = pagina.relative_to(raiz).as_posix()
        partes.append(f'<pagina caminho="{relativo}">')
        for numero, linha in enumerate(pagina.read_text(encoding="utf-8").splitlines(), 1):
            partes.append(f"L{numero} [hash={hash_linha(linha)}] {linha}")
        partes.append("</pagina>")
    return "\n".join(partes)


def montar_prompt_compilador(fonte, source_id, contexto, fontes_validas, erros=None):
    """Preenche o prompt do compilador sem reinterpretar o texto da fonte."""
    template = (ROOT / "prompts/compilador.md").read_text(encoding="utf-8")
    valores = {
        "{{fonte_id}}": source_id,
        "{{fonte_tipo}}": fonte["tipo"],
        "{{fonte_titulo}}": fonte["titulo"],
        "{{fonte_data}}": fonte["data"],
        "{{contexto_wiki}}": contexto,
        FONTES_VALIDAS_PLACEHOLDER: ", ".join(sorted(fontes_validas)),
    }
    for marcador, valor in valores.items():
        template = template.replace(marcador, valor)
    prompt = template.replace("{{fonte_texto}}", fonte["texto"])
    if erros:
        bloco = "\n".join(f"- {erro}" for erro in erros)
        prompt += (
            "\n\n## Erros da tentativa anterior\n\n"
            f"{bloco}\n\n"
            "Corrija todos os erros e responda de novo, apenas com o JSON."
        )
    return prompt


def _carregar_resposta(texto):
    """Interpreta JSON puro ou cercado por bloco Markdown."""
    if not isinstance(texto, str):
        raise ErroResposta("resposta do modelo não é texto")
    limpo = texto.strip()
    if limpo.startswith("```"):
        limpo = re.sub(r"^```(?:json)?\s*", "", limpo)
        limpo = re.sub(r"\s*```$", "", limpo)
    inicio, fim = limpo.find("{"), limpo.rfind("}")
    if inicio == -1 or fim == -1 or fim < inicio:
        raise ErroResposta("resposta do modelo não contém objeto JSON")
    try:
        dados = json.loads(limpo[inicio : fim + 1])
    except json.JSONDecodeError as erro:
        raise ErroResposta(f"JSON inválido na resposta do modelo: {erro}") from erro
    if not isinstance(dados, dict):
        raise ErroResposta("resposta do modelo precisa ser um objeto JSON")
    return dados


def montar_prompt_classificador(titulo, texto):
    """Preenche o prompt do classificador tratando título e texto como dado."""
    template = (ROOT / "prompts/classificador-ingest.md").read_text(encoding="utf-8")
    template = template.replace("{{fonte_titulo}}", str(titulo))
    return template.replace("{{fonte_texto}}", str(texto))


def _textos_unicos(valor, campo):
    """Valida uma lista de strings, remove vazios e duplicatas preservando ordem."""
    if not isinstance(valor, list) or not all(isinstance(item, str) for item in valor):
        raise ErroResposta(f"'{campo}' precisa ser uma lista de textos")
    vistos = set()
    itens = []
    for item in valor:
        limpo = item.strip()
        if limpo and limpo not in vistos:
            vistos.add(limpo)
            itens.append(limpo)
    return itens


def _validar_classificacao(dados):
    """Valida o contrato do classificador e devolve só os campos conhecidos."""
    if not isinstance(dados.get("injecao"), bool):
        raise ErroResposta("'injecao' precisa ser booleano")
    return {
        "injecao": dados["injecao"],
        "sinais": _textos_unicos(dados.get("sinais", []), "sinais"),
        "nomes": _textos_unicos(dados.get("nomes", []), "nomes"),
    }


def classificar(titulo, texto, chamar_modelo=None):
    """Faz uma única chamada ao modelo e devolve `injecao`, `sinais` e `nomes`.

    `chamar_modelo(prompt) -> str` é injetável; por padrão usa o adaptador
    Gemini em modo JSON com o schema do classificador.
    """
    if chamar_modelo is None:
        def chamar_modelo(prompt):
            return chamar_gemini_json(prompt, response_schema=ESQUEMA_CLASSIFICADOR)

    prompt = montar_prompt_classificador(titulo, texto)
    resposta = chamar_modelo(prompt)
    return _validar_classificacao(_carregar_resposta(resposta))


MARCADOR_NOME = "[nome removido]"


def _nomes_ordenados(nomes):
    """Limpa e ordena nomes do mais longo para o mais curto, sem repetições."""
    if not nomes:
        return []
    if not isinstance(nomes, (list, tuple)):
        raise TypeError("'nomes' precisa ser uma lista de textos")
    vistos = set()
    limpos = []
    for nome in nomes:
        if not isinstance(nome, str):
            raise TypeError("'nomes' precisa conter apenas textos")
        nome = nome.strip()
        if nome and nome not in vistos:
            vistos.add(nome)
            limpos.append(nome)
    return sorted(limpos, key=len, reverse=True)


def _substituir_com_contagem(texto, nomes):
    total = 0
    for nome in _nomes_ordenados(nomes):
        total += texto.count(nome)
        texto = texto.replace(nome, MARCADOR_NOME)
    return texto, total


def substituir_nomes(texto, nomes):
    """Troca cada span exato de nome por `[nome removido]`, em todas as ocorrências.

    A comparação é sensível a maiúsculas e usa o span integral: `Rosa` vira
    `[nome removido]`, mas `camiseta rosa` não muda.
    """
    return _substituir_com_contagem(texto, nomes)[0]


def contar_substituicoes(texto, nomes):
    """Quantidade de ocorrências exatas substituídas por `substituir_nomes`."""
    return _substituir_com_contagem(texto, nomes)[1]


def _resultado_triagem(
    resultado, motivo=None, injecao=None, sinais=None, nomes=None, texto_derivado=None
):
    return {
        "resultado": resultado,
        "motivo": motivo,
        "injecao": injecao,
        "sinais": list(sinais or []),
        "nomes": list(nomes or []),
        "texto_derivado": texto_derivado,
    }


def _evento_triagem(resultado):
    """Payload de notificação sem nomes e sem texto da fonte."""
    return {
        "resultado": resultado["resultado"],
        "motivo": resultado["motivo"],
        "injecao": resultado["injecao"],
        "sinais": list(resultado["sinais"]),
    }


def _aplicar_efeitos(resultado, atualizar_status, notificar, liberar_lock):
    if atualizar_status is not None:
        atualizar_status(resultado["resultado"], resultado["motivo"])
    if notificar is not None:
        notificar(_evento_triagem(resultado))
    if liberar_lock is not None:
        liberar_lock()


def triar(
    titulo,
    texto,
    chamar_modelo=None,
    *,
    liberado_pela_gestao=False,
    atualizar_status=None,
    notificar=None,
    liberar_lock=None,
):
    """Executa a triagem de uma fonte e devolve resultado, nomes e texto derivado.

    - Pré-filtro determinístico (pulado com liberação da gestão): um acerto
      marca `quarentena`/`prefiltro` sem chamar modelo.
    - Classificador: uma única chamada. Verdicto de injeção verdadeiro marca
      `quarentena`/`injecao`; com liberação, o veredito é ignorado.
    - Falha de formato ou de API marca `falhou`/`classificacao`.
    - Nomes são substituídos por span exato em `texto_derivado`.

    Os efeitos colaterais são injetáveis: `atualizar_status(status, motivo)`,
    `notificar(evento)` (sem nomes nem texto bruto) e `liberar_lock()`.
    Retorna um dicionário com `resultado`, `motivo`, `injecao`, `sinais`,
    `nomes` e `texto_derivado`.
    """
    if not liberado_pela_gestao and prefiltro(texto, titulo):
        resultado = _resultado_triagem(
            "quarentena", "prefiltro", sinais=["prefiltro"]
        )
        _aplicar_efeitos(resultado, atualizar_status, notificar, liberar_lock)
        return resultado

    try:
        classificacao = classificar(titulo, texto, chamar_modelo)
    except (ErroApi, ErroResposta):
        resultado = _resultado_triagem("falhou", "classificacao")
        _aplicar_efeitos(resultado, atualizar_status, notificar, liberar_lock)
        return resultado

    nomes = classificacao["nomes"]
    derivado = substituir_nomes(texto, nomes)
    if classificacao["injecao"] and not liberado_pela_gestao:
        resultado = _resultado_triagem(
            "quarentena",
            "injecao",
            injecao=True,
            sinais=classificacao["sinais"],
            nomes=nomes,
            texto_derivado=derivado,
        )
        _aplicar_efeitos(resultado, atualizar_status, notificar, liberar_lock)
        return resultado

    return _resultado_triagem(
        "ok",
        None,
        injecao=classificacao["injecao"],
        sinais=classificacao["sinais"],
        nomes=nomes,
        texto_derivado=derivado,
    )


def _normalizar_secao(secao):
    return str(secao or "").strip().removeprefix("## ").strip()


def _formatar_linha(texto):
    texto = str(texto).strip()
    if not texto:
        raise ErroOperacao("operação adicionar_linha com texto vazio")
    if texto.startswith("- ") or re.match(r"^\d+\. ", texto):
        return texto
    return f"- {texto}"


def _validar_citacoes(texto, fontes_validas, exigir=True):
    citadas = CITACAO.findall(texto)
    if exigir and not citadas:
        raise ErroOperacao("linha sem citação [fonte: ID]")
    desconhecidas = sorted(set(citadas) - set(fontes_validas))
    if desconhecidas:
        raise ErroOperacao(f"citação de fonte desconhecida: {', '.join(desconhecidas)}")
    return citadas


def contem_nome(texto, nomes):
    """Indica se `texto` contém algum nome, sem caixa e por palavra inteira."""
    for nome in _nomes_ordenados(nomes):
        padrao = r"(?<!\w)" + re.escape(nome) + r"(?!\w)"
        if re.search(padrao, str(texto), re.IGNORECASE):
            return True
    return False


def _strings_de(valor):
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, list):
        textos = []
        for item in valor:
            textos.extend(_strings_de(item))
        return textos
    if isinstance(valor, dict):
        textos = []
        for item in valor.values():
            textos.extend(_strings_de(item))
        return textos
    return []


def _textos_gerados(dados):
    """Textos da proposta que virarão páginas, índice, log ou pendências."""
    textos = []
    for campo in ("fatos_novos", "mudancas"):
        for item in dados.get(campo, []):
            if isinstance(item, dict):
                textos.append(str(item.get("texto", "")))
    for item in dados.get("lacunas", []):
        if isinstance(item, dict):
            textos.append(str(item.get("texto", "")))
    for item in dados.get("contradicoes", []):
        if isinstance(item, dict):
            textos.append(str(item.get("resumo", "")))
    for operacao in dados.get("operacoes", []):
        if not isinstance(operacao, dict):
            continue
        if "texto" in operacao:
            textos.append(str(operacao.get("texto", "")))
        if operacao.get("tipo") == "criar_pagina":
            textos.extend(_strings_de(operacao.get("secoes") or {}))
    return textos


def _validar_textos_gerados(dados, nomes):
    if not nomes:
        return
    for texto in _textos_gerados(dados):
        if contem_nome(texto, nomes):
            raise ErroPii("nome de pessoa em texto gerado")


def _indice_da_secao(linhas, secao):
    alvo = f"## {secao}"
    for indice, linha in enumerate(linhas):
        if linha.strip() == alvo:
            return indice
    raise ErroOperacao(f"seção não encontrada: {secao!r}")


def _secao_da_linha(linhas, numero):
    for indice in range(min(numero, len(linhas)) - 1, -1, -1):
        if linhas[indice].startswith("## "):
            return _normalizar_secao(linhas[indice])
    return ""


def _inserir_na_secao(linhas, secao, nova_linha):
    inicio = _indice_da_secao(linhas, secao)
    fim = len(linhas)
    for indice in range(inicio + 1, len(linhas)):
        if linhas[indice].startswith("## "):
            fim = indice
            break
    posicao = fim
    while posicao > inicio + 1 and linhas[posicao - 1].strip() == "":
        posicao -= 1
    linhas.insert(posicao, nova_linha)
    if posicao + 1 < len(linhas) and linhas[posicao + 1].startswith("## "):
        linhas.insert(posicao + 1, "")


def _substituir_secao(linhas, secao, novas_linhas):
    inicio = _indice_da_secao(linhas, secao)
    fim = len(linhas)
    for indice in range(inicio + 1, len(linhas)):
        if linhas[indice].startswith("## "):
            fim = indice
            break
    novas = [""] + list(novas_linhas)
    if fim < len(linhas):
        novas.append("")
    linhas[inicio + 1 : fim] = novas


def _sincronizar_fontes(linhas):
    """Acrescenta ao frontmatter todo ID citado no corpo."""
    if not linhas or linhas[0].strip() != "---":
        return
    try:
        fim = linhas.index("---", 1)
    except ValueError:
        return
    citadas = set(CITACAO.findall("\n".join(linhas[fim + 1 :])))
    for indice in range(1, fim):
        casamento = re.match(r"^fontes:\s*\[(.*)\]\s*$", linhas[indice])
        if not casamento:
            continue
        atuais = [item.strip() for item in casamento.group(1).split(",") if item.strip()]
        novas = sorted(citadas - set(atuais))
        if novas:
            linhas[indice] = "fontes: [" + ", ".join(atuais + novas) + "]"
        return


def _definir_campo(linhas, campo, valor):
    if not linhas or linhas[0].strip() != "---":
        return
    try:
        fim = linhas.index("---", 1)
    except ValueError:
        return
    for indice in range(1, fim):
        if re.match(rf"^{re.escape(campo)}:", linhas[indice]):
            linhas[indice] = f"{campo}: {valor}"
            return
    linhas.insert(fim, f"{campo}: {valor}")


def _atualizar_frontmatter_tocado(linhas, data_documento):
    """Volta a página tocada a rascunho e atualiza a data, mantendo confiança."""
    if not linhas or linhas[0].strip() != "---":
        return
    _definir_campo(linhas, "status", "rascunho")
    _definir_campo(linhas, "validado_por", "null")
    _definir_campo(linhas, "validado_em", "null")
    if not data_documento:
        return
    try:
        nova = date.fromisoformat(str(data_documento))
    except ValueError:
        return
    atual = None
    try:
        fim = linhas.index("---", 1)
    except ValueError:
        return
    for indice in range(1, fim):
        casamento = re.match(r"^atualizado_em:\s*(\S+)", linhas[indice])
        if casamento:
            try:
                atual = date.fromisoformat(casamento.group(1))
            except ValueError:
                atual = None
            break
    if atual is None or nova > atual:
        _definir_campo(linhas, "atualizado_em", nova.isoformat())


def _caminho_da_pagina(raiz, pagina):
    if not isinstance(pagina, str) or not pagina.strip():
        raise ErroOperacao("operação sem 'pagina'")
    relativo = Path(pagina)
    if relativo.is_absolute() or ".." in relativo.parts:
        raise ErroOperacao(f"caminho de página inválido: {pagina!r}")
    alvo = (Path(raiz) / relativo).resolve()
    wiki = (Path(raiz) / "wiki").resolve()
    if not alvo.is_relative_to(wiki):
        raise ErroOperacao(f"página fora da wiki: {pagina!r}")
    if not alvo.is_file():
        raise ErroOperacao(f"página não encontrada: {pagina!r}")
    return alvo


def _caminho_novo(raiz, caminho):
    if not isinstance(caminho, str) or not caminho.strip():
        raise ErroOperacao("criar_pagina sem 'caminho'")
    relativo = Path(caminho)
    if relativo.is_absolute() or ".." in relativo.parts or relativo.suffix != ".md":
        raise ErroOperacao(f"caminho de página nova inválido: {caminho!r}")
    partes = relativo.parts
    if (
        len(partes) != 3
        or partes[0] != "wiki"
        or partes[1] not in PASTAS_POR_TIPO_PAGINA
    ):
        raise ErroOperacao(
            "página nova só pode ficar em wiki/processos ou wiki/decisoes"
        )
    alvo = (Path(raiz) / relativo).resolve()
    wiki = (Path(raiz) / "wiki").resolve()
    if not alvo.is_relative_to(wiki):
        raise ErroOperacao(f"página fora da wiki: {caminho!r}")
    if alvo.exists():
        raise ErroOperacao(f"página nova já existe: {caminho!r}")
    return relativo.as_posix(), alvo


def _limpar_titulo(titulo, nomes):
    titulo = str(titulo or "").strip()
    if not titulo:
        raise ErroOperacao("criar_pagina sem 'titulo'")
    for nome in _nomes_ordenados(nomes):
        padrao = r"(?<!\w)" + re.escape(nome) + r"(?!\w)"
        titulo = re.sub(padrao, MARCADOR_NOME, titulo, flags=re.IGNORECASE)
    titulo = re.sub(r"\s+", " ", titulo).strip()
    if not titulo:
        raise ErroOperacao("título da página nova ficou vazio")
    return titulo


def _linhas_de_conteudo(valor, formatar=True):
    if isinstance(valor, str):
        itens = [valor]
    elif isinstance(valor, list):
        itens = valor
    else:
        raise ErroOperacao("conteúdo de seção precisa ser texto ou lista de textos")
    linhas = []
    for item in itens:
        if not isinstance(item, str):
            raise ErroOperacao("conteúdo de seção precisa conter apenas textos")
        for pedaco in item.splitlines():
            pedaco = pedaco.strip()
            if not pedaco:
                continue
            linhas.append(_formatar_linha(pedaco) if formatar else pedaco)
    return linhas


def _proximo_id_pendencia(linhas):
    maior = 0
    for linha in linhas:
        for achado in re.findall(r"\bP-(\d+)\b", linha):
            maior = max(maior, int(achado))
    return maior + 1


def _linha_pendencia(identificador, resumo, fontes):
    resumo = " ".join(str(resumo).split()).replace("|", "/")
    citacoes = " ".join(f"[fonte: {fonte}]" for fonte in fontes)
    return (
        f"| {identificador} | {resumo} {citacoes} | "
        "Gerado pelo ingest; revisar a fonte. | Gestão |"
    )


def _inserir_linha_tabela(linhas, nova_linha):
    indices = [i for i, linha in enumerate(linhas) if linha.lstrip().startswith("|")]
    if indices:
        linhas.insert(max(indices) + 1, nova_linha)
        return
    posicao = 1
    while posicao < len(linhas) and not linhas[posicao].startswith("## "):
        posicao += 1
    linhas[posicao:posicao] = [
        "",
        "| ID | Problema | Tratamento atual | Quem resolve |",
        "|---|---|---|---|",
        nova_linha,
        "",
    ]


def _adicionar_ao_indice(linhas, caminho_relativo, titulo):
    if f"({caminho_relativo})" in "\n".join(linhas):
        return
    pasta = Path(caminho_relativo).parts[0]
    secao = "## Processos" if pasta == "processos" else "## Decisões"
    try:
        inicio = _indice_da_secao(linhas, secao.removeprefix("## "))
    except ErroOperacao:
        linhas.extend(["", secao, ""])
        inicio = len(linhas) - 2
    fim = len(linhas)
    for indice in range(inicio + 1, len(linhas)):
        if linhas[indice].startswith("## "):
            fim = indice
            break
    posicao = fim
    while posicao > inicio + 1 and linhas[posicao - 1].strip() == "":
        posicao -= 1
    linhas.insert(posicao, f"- [{titulo}]({caminho_relativo})")
    if posicao + 1 < len(linhas) and linhas[posicao + 1].startswith("## "):
        linhas.insert(posicao + 1, "")


def _adicionar_log(linhas, data_documento, source_id, operacoes, novas, contradicoes):
    if linhas and linhas[-1].strip():
        linhas.append("")
    data = data_documento or "data desconhecida"
    linhas.append(f"## {data}: ingestão de {source_id}")
    linhas.append("")
    linhas.append(f"- Operações aplicadas: {operacoes}.")
    if novas:
        linhas.append(f"- Páginas novas: {novas}.")
    if contradicoes:
        linhas.append(f"- Contradições registradas: {contradicoes}.")


def _fontes_da_operacao(operacao, source_id, fontes_validas):
    citadas = CITACAO.findall(str(operacao.get("texto", "")))
    if citadas:
        desconhecidas = set(citadas) - set(fontes_validas)
        if desconhecidas:
            raise ErroOperacao(
                "operação em contatos cita fonte desconhecida: "
                + ", ".join(sorted(desconhecidas))
            )
        return sorted(set(citadas))
    if source_id and source_id in fontes_validas:
        return [source_id]
    raise ErroOperacao("operação em contatos sem fonte para a pendência")


def _validar_contradicoes(contradicoes, fontes_validas):
    itens = []
    if contradicoes is None:
        return itens
    if not isinstance(contradicoes, list):
        raise ErroOperacao("'contradicoes' precisa ser uma lista")
    for contradicao in contradicoes:
        if not isinstance(contradicao, dict):
            raise ErroOperacao("contradição precisa ser um objeto JSON")
        resumo = str(contradicao.get("resumo") or "").strip()
        if not resumo:
            raise ErroOperacao("contradição sem resumo")
        fontes = contradicao.get("fontes")
        if not isinstance(fontes, list) or not fontes:
            raise ErroOperacao("contradição sem linha P-xx: falta a fonte")
        for fonte_id in fontes:
            if not isinstance(fonte_id, str) or fonte_id not in fontes_validas:
                raise ErroOperacao(
                    f"contradição cita fonte desconhecida: {fonte_id!r}"
                )
        itens.append({"resumo": resumo, "fontes": sorted(set(fontes))})
    return itens


def _snapshot_wiki(raiz):
    wiki = Path(raiz) / "wiki"
    return {
        pagina.relative_to(wiki).as_posix(): pagina.read_text(encoding="utf-8")
        for pagina in wiki.rglob("*.md")
    }


def _restaurar_wiki(raiz, snapshot):
    wiki = Path(raiz) / "wiki"
    atuais = {pagina.relative_to(wiki).as_posix() for pagina in wiki.rglob("*.md")}
    for relativo in sorted(atuais - set(snapshot)):
        (wiki / relativo).unlink()
    for relativo, texto in snapshot.items():
        destino = wiki / relativo
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(texto, encoding="utf-8")


def _normalizar_lint(resultado):
    if isinstance(resultado, dict):
        return {
            "erros": list(resultado.get("erros", []) or []),
            "avisos": list(resultado.get("avisos", []) or []),
        }
    return {"erros": list(resultado or []), "avisos": []}


def _erros_de_lint(resultado, source_id):
    itens = resultado.get("erros", []) if isinstance(resultado, dict) else resultado
    erros = []
    for erro in itens or []:
        if isinstance(erro, dict):
            problema = erro.get("problema")
            detalhe = str(erro.get("detalhe", ""))
            if problema == "Fonte desconhecida" and source_id and source_id in detalhe:
                continue
            pagina = erro.get("pagina", "?")
            mensagem = erro.get("mensagem") or detalhe or problema or "erro de lint"
            erros.append(f"{pagina}: {mensagem}")
        else:
            erros.append(str(erro))
    return erros


def _erros_lint(raiz, source_id, lint=None):
    if lint is None:
        from tools.lint_wiki import revisar

        lint = revisar
    return _erros_de_lint(_normalizar_lint(lint(raiz)), source_id)


def _validar_proposta(dados):
    if not isinstance(dados, dict):
        raise ErroResposta("resposta do modelo precisa ser um objeto JSON")
    for campo in ("fatos_novos", "mudancas", "contradicoes", "lacunas", "operacoes"):
        valor = dados.setdefault(campo, [])
        if not isinstance(valor, list):
            raise ErroResposta(f"'{campo}' precisa ser uma lista")
    for operacao in dados["operacoes"]:
        if not isinstance(operacao, dict):
            raise ErroResposta("operação precisa ser um objeto JSON")
        if operacao.get("tipo") not in TIPOS_OPERACAO:
            raise ErroOperacao(f"operação não suportada: {operacao.get('tipo')!r}")
    for contradicao in dados["contradicoes"]:
        if not isinstance(contradicao, dict):
            raise ErroResposta("contradição precisa ser um objeto JSON")
    return dados


def aplicar_operacoes(
    raiz,
    operacoes,
    fontes_validas,
    *,
    manifesto=None,
    fonte=None,
    source_id=None,
    data_documento=None,
    nomes=None,
    contradicoes=None,
):
    """Aplica a proposta inteira de forma atômica: valida tudo antes de gravar.

    Aceita `adicionar_linha`, `marcar_substituida`, `atualizar_resumo` e
    `criar_pagina`, além das contradições que viram linhas `P-xx`. O código
    escreve `index.md`, `log.md` e `pendencias.md`; o modelo nunca os edita.
    """
    if not isinstance(operacoes, list):
        raise ErroOperacao("'operacoes' precisa ser uma lista")

    raiz = Path(raiz).resolve()
    if manifesto is None:
        manifesto = carregar_manifesto(raiz)
    mapa = {item["id"]: item for item in manifesto.get("fontes", [])}
    nomes = list(nomes or [])
    tipo_origem = (fonte or {}).get("tipo")
    if tipo_origem is None and source_id in mapa:
        tipo_origem = mapa[source_id].get("tipo")
    data_documento = data_documento or (fonte or {}).get("data")
    if data_documento is None and source_id and source_id in mapa:
        data_documento = mapa[source_id].get("data")

    paginas = {}
    alteradas = []
    novas = {}
    novos_titulos = []
    aplicadas = []
    substituicoes = 0
    pendencias_novas = []
    paginas_com_marcar = set()
    pendencias_contato = 0

    def info_fonte(fonte_id):
        if fonte_id in mapa:
            return mapa[fonte_id]
        if fonte is not None and fonte_id == source_id:
            return {
                "id": fonte_id,
                "autoridade": AUTORIDADE_POR_TIPO.get(fonte["tipo"]),
                "data": fonte["data"],
            }
        return None

    def carregar(caminho):
        if caminho not in paginas:
            paginas[caminho] = caminho.read_text(encoding="utf-8").splitlines()
        return paginas[caminho]

    def marcar_alterada(caminho):
        if caminho not in alteradas:
            alteradas.append(caminho)

    for operacao in operacoes:
        if isinstance(operacao, dict) and operacao.get("tipo") == "marcar_substituida":
            referencia_pagina = operacao.get("pagina")
            if isinstance(referencia_pagina, str):
                paginas_com_marcar.add(Path(referencia_pagina).as_posix())

    for operacao in operacoes:
        if not isinstance(operacao, dict):
            raise ErroOperacao("operação precisa ser um objeto JSON")
        tipo = operacao.get("tipo")
        if tipo not in TIPOS_OPERACAO:
            raise ErroOperacao(f"operação não suportada: {tipo!r}")
        _validar_textos_gerados({"operacoes": [operacao]}, nomes)

        pagina_ref = operacao.get("pagina")
        if (
            tipo in ("adicionar_linha", "marcar_substituida", "atualizar_resumo")
            and isinstance(pagina_ref, str)
            and Path(pagina_ref).as_posix() == ARQUIVO_CONTATOS
        ):
            pendencias_novas.append(
                {
                    "resumo": "Mudança em contatos sugerida pelo ingest; revisar manualmente.",
                    "fontes": _fontes_da_operacao(operacao, source_id, fontes_validas),
                }
            )
            pendencias_contato += 1
            continue

        if tipo == "criar_pagina":
            if tipo_origem == "relato":
                raise ErroOperacao("fonte relato não pode criar página")
            caminho_relativo, alvo = _caminho_novo(raiz, operacao.get("caminho"))
            pasta = Path(caminho_relativo).parts[1]
            if operacao.get("tipo_pagina") != PASTAS_POR_TIPO_PAGINA[pasta]:
                raise ErroOperacao(
                    "tipo_pagina precisa bater com a pasta da página nova"
                )
            titulo = _limpar_titulo(operacao.get("titulo"), nomes)
            secoes = operacao.get("secoes") or {}
            if not isinstance(secoes, dict):
                raise ErroOperacao("'secoes' da página nova precisa ser um objeto")
            conteudo = {}
            for chave, valor in secoes.items():
                nome_secao = _normalizar_secao(chave)
                if nome_secao == "Passo a passo" and pasta != "processos":
                    raise ErroOperacao(
                        "página de decisão não aceita 'Passo a passo'"
                    )
                if nome_secao not in SECOES_OBRIGATORIAS and nome_secao != "Passo a passo":
                    raise ErroOperacao(f"seção desconhecida na página nova: {nome_secao!r}")
                linhas_secao = _linhas_de_conteudo(
                    valor, formatar=nome_secao != "Resumo"
                )
                for linha_secao in linhas_secao:
                    _validar_citacoes(
                        linha_secao,
                        fontes_validas,
                        exigir=nome_secao in SECOES_COM_CITACAO,
                    )
                conteudo[nome_secao] = linhas_secao

            ordem = list(SECOES_OBRIGATORIAS)
            if pasta == "processos":
                ordem.insert(2, "Passo a passo")
            corpo = []
            for nome_secao in ordem:
                corpo.append(f"## {nome_secao}")
                corpo.extend(conteudo.get(nome_secao, []))
                corpo.append("")
            citadas = sorted(set(CITACAO.findall("\n".join(corpo))))
            if not data_documento:
                raise ErroOperacao("criar_pagina exige a data do documento")
            identificador = alvo.stem
            novas[alvo] = "\n".join(
                [
                    "---",
                    f"id: {identificador}",
                    f"titulo: {titulo}",
                    f"tipo: {PASTAS_POR_TIPO_PAGINA[pasta]}",
                    "status: rascunho",
                    "confianca: baixa",
                    "fontes: [" + ", ".join(citadas) + "]",
                    f"atualizado_em: {data_documento}",
                    "validado_por: null",
                    "validado_em: null",
                    "---",
                    "",
                    f"# {titulo}",
                    "",
                    *corpo,
                ]
            )
            novos_titulos.append((caminho_relativo, titulo))
            aplicadas.append(
                {"pagina": caminho_relativo, "texto": f"página nova: {titulo}"}
            )
            continue

        if (
            isinstance(pagina_ref, str)
            and Path(pagina_ref).as_posix() in ARQUIVOS_DO_CODIGO
        ):
            raise ErroOperacao(
                f"{Path(pagina_ref).as_posix()} é escrito pelo código; "
                "o modelo não edita"
            )
        caminho = _caminho_da_pagina(raiz, pagina_ref)
        linhas = carregar(caminho)

        if tipo == "adicionar_linha":
            secao = _normalizar_secao(operacao.get("secao"))
            texto = str(operacao.get("texto") or "")
            nova_linha = _formatar_linha(texto)
            linha = operacao.get("linha")
            hash_referencia = operacao.get("hash")
            relativo_pagina = caminho.relative_to(raiz).as_posix()
            exigir_citacao = (
                secao in SECOES_COM_CITACAO
                or relativo_pagina == "wiki/glossario.md"
            )
            if linha is not None:
                if isinstance(linha, bool) or not isinstance(linha, int):
                    raise ErroOperacao("'linha' precisa ser um inteiro")
                if not (1 <= linha <= len(linhas)):
                    raise ErroOperacao(f"linha fora da página: {linha}")
                if not isinstance(hash_referencia, str) or not hash_referencia:
                    raise ErroOperacao("operação com 'linha' precisa informar 'hash'")
                if hash_linha(linhas[linha - 1]) != hash_referencia:
                    raise ErroOperacao(
                        f"linha {linha} desatualizada: hash divergente "
                        f"(esperado {hash_linha(linhas[linha - 1])})"
                    )
                exigir_citacao = exigir_citacao or (
                    _secao_da_linha(linhas, linha) in SECOES_COM_CITACAO
                )
            elif hash_referencia:
                raise ErroOperacao("'hash' só é aceito junto de 'linha'")
            _validar_citacoes(texto, fontes_validas, exigir=exigir_citacao)
            if linha is not None:
                linhas.insert(linha, nova_linha)
            elif relativo_pagina == "wiki/glossario.md":
                linhas.append(nova_linha)
            else:
                _inserir_na_secao(linhas, secao, nova_linha)
            marcar_alterada(caminho)
            aplicadas.append(
                {
                    "pagina": caminho.relative_to(raiz).as_posix(),
                    "texto": nova_linha,
                }
            )
            continue

        if tipo == "marcar_substituida":
            nova_fonte = operacao.get("nova_fonte")
            if not isinstance(nova_fonte, str) or not nova_fonte:
                raise ErroOperacao("marcar_substituida sem 'nova_fonte'")
            nova_info = info_fonte(nova_fonte)
            if nova_info is None:
                raise ErroOperacao(f"fonte nova desconhecida: {nova_fonte}")
            data_informada = operacao.get("data")
            data = str(
                data_informada
                or data_documento
                or nova_info.get("data")
                or ""
            )
            try:
                nova_data = date.fromisoformat(data)
            except ValueError as erro:
                raise ErroOperacao(f"marcar_substituida com data inválida: {data!r}") from erro
            if (
                data_informada is not None
                and nova_info.get("data")
                and str(data_informada) != str(nova_info["data"])
            ):
                raise ErroOperacao(
                    "marcar_substituida com data diferente da data da fonte nova"
                )
            nova_autoridade = nova_info.get("autoridade")
            if nova_autoridade not in AUTORIDADE:
                raise ErroOperacao(
                    f"fonte {nova_fonte} não pode sustentar afirmação"
                )

            linha = operacao.get("linha")
            hash_referencia = operacao.get("hash")
            if isinstance(linha, bool) or not isinstance(linha, int):
                raise ErroOperacao("'linha' precisa ser um inteiro")
            if not (1 <= linha <= len(linhas)):
                raise ErroOperacao(f"linha fora da página: {linha}")
            if not isinstance(hash_referencia, str) or not hash_referencia:
                raise ErroOperacao("marcar_substituida precisa informar 'hash'")
            if hash_linha(linhas[linha - 1]) != hash_referencia:
                raise ErroOperacao(
                    f"linha {linha} desatualizada: hash divergente "
                    f"(esperado {hash_linha(linhas[linha - 1])})"
                )
            linha_atual = linhas[linha - 1]
            citadas = CITACAO.findall(linha_atual)
            if not citadas:
                raise ErroOperacao("linha substituída precisa citar uma fonte")
            maior_rank = 0
            maior_data = None
            for fonte_citada in citadas:
                informacao = info_fonte(fonte_citada)
                if informacao is None:
                    raise ErroOperacao(
                        f"linha cita fonte desconhecida: {fonte_citada}"
                    )
                autoridade_citada = informacao.get("autoridade")
                if autoridade_citada not in AUTORIDADE:
                    raise ErroOperacao(
                        f"linha cita fonte que não sustenta afirmação: {fonte_citada}"
                    )
                maior_rank = max(maior_rank, AUTORIDADE[autoridade_citada])
                try:
                    data_citada = date.fromisoformat(str(informacao.get("data", "")))
                except ValueError:
                    continue
                if maior_data is None or data_citada > maior_data:
                    maior_data = data_citada
            if AUTORIDADE[nova_autoridade] < maior_rank:
                raise ErroOperacao(
                    "fonte de autoridade menor não altera linha de autoridade maior"
                )
            if maior_data is None or nova_data <= maior_data:
                raise ErroOperacao(
                    "marcar_substituida exige data estritamente maior que a maior data citada"
                )
            marca = f" (substituída por [fonte: {nova_fonte}] em {data})"
            if linha_atual.rstrip().endswith(marca):
                raise ErroOperacao("linha já foi marcada como substituída")
            linhas[linha - 1] = linha_atual.rstrip() + marca
            substituicoes += 1
            marcar_alterada(caminho)
            aplicadas.append(
                {
                    "pagina": caminho.relative_to(raiz).as_posix(),
                    "texto": marca.strip(),
                }
            )
            continue

        if tipo == "atualizar_resumo":
            caminho_relativo = caminho.relative_to(raiz).as_posix()
            if caminho_relativo not in paginas_com_marcar:
                raise ErroOperacao(
                    "atualizar_resumo exige marcar_substituida válido para a mesma página"
                )
            texto = operacao.get("texto")
            if not isinstance(texto, str) or not texto.strip():
                raise ErroOperacao("atualizar_resumo com texto vazio")
            novas_linhas = _linhas_de_conteudo(texto, formatar=False)
            for linha_resumo in novas_linhas:
                _validar_citacoes(linha_resumo, fontes_validas, exigir=False)
            _substituir_secao(linhas, "Resumo", novas_linhas)
            marcar_alterada(caminho)
            aplicadas.append(
                {
                    "pagina": caminho_relativo,
                    "texto": "resumo atualizado",
                }
            )
            continue

    contradicoes_validas = _validar_contradicoes(contradicoes, fontes_validas)
    for contradicao in contradicoes_validas:
        if contem_nome(contradicao["resumo"], nomes):
            raise ErroPii("nome de pessoa em texto gerado")
    pendencias_novas.extend(contradicoes_validas)

    if pendencias_novas:
        caminho_pendencias = raiz / "wiki/pendencias.md"
        if not caminho_pendencias.exists():
            raise ErroOperacao("wiki/pendencias.md ausente")
        linhas_pendencias = carregar(caminho_pendencias)
        proximo = _proximo_id_pendencia(linhas_pendencias)
        for item in pendencias_novas:
            identificador = f"P-{proximo:02d}"
            proximo += 1
            nova_linha = _linha_pendencia(
                identificador, item["resumo"], item["fontes"]
            )
            _inserir_linha_tabela(linhas_pendencias, nova_linha)
            pendencias_novas_resultado = {
                "id": identificador,
                "linha": nova_linha,
                "fontes": item["fontes"],
            }
            item["resultado"] = pendencias_novas_resultado
        marcar_alterada(caminho_pendencias)

    if novos_titulos:
        caminho_indice = raiz / "wiki/index.md"
        if not caminho_indice.exists():
            raise ErroOperacao("wiki/index.md ausente")
        linhas_indice = carregar(caminho_indice)
        for caminho_relativo, titulo in novos_titulos:
            _adicionar_ao_indice(
                linhas_indice,
                caminho_relativo.removeprefix("wiki/"),
                titulo,
            )
        marcar_alterada(caminho_indice)

    if aplicadas or pendencias_novas:
        caminho_log = raiz / "wiki/log.md"
        if caminho_log.exists():
            linhas_log = carregar(caminho_log)
            _adicionar_log(
                linhas_log,
                data_documento,
                source_id or "fonte",
                len(aplicadas),
                len(novas),
                len(contradicoes_validas),
            )
            marcar_alterada(caminho_log)

    for caminho in alteradas:
        _sincronizar_fontes(paginas[caminho])
        _atualizar_frontmatter_tocado(paginas[caminho], data_documento)
        caminho.write_text("\n".join(paginas[caminho]) + "\n", encoding="utf-8")

    for alvo, texto in novas.items():
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(texto + "\n", encoding="utf-8")

    return {
        "aplicadas": aplicadas,
        "substituicoes": substituicoes,
        "pendencias": [
            item["resultado"] for item in pendencias_novas if "resultado" in item
        ],
        "novas_paginas": [alvo.relative_to(raiz).as_posix() for alvo in novas],
        "pendencias_contato": pendencias_contato,
    }


def compilar(
    fonte,
    raiz,
    chamar_modelo,
    *,
    source_id=None,
    nomes=None,
    max_tentativas=MAX_TENTATIVAS,
    lint=None,
):
    """Compila uma fonte local com até `max_tentativas` tentativas.

    Cada tentativa recebe a lista exata de erros da anterior. A proposta é
    aplicada de forma atômica, o lint roda no repositório e, se algo falhar,
    a wiki volta ao estado anterior antes da próxima tentativa. Depois de
    esgotar as tentativas, levanta `ErroCompilacao` com `motivo` (`lint`,
    `schema`, `pii` ou `compilacao`) e a lista exata de erros.
    """
    fonte = validar_fonte(fonte)
    raiz = Path(raiz)
    manifesto = carregar_manifesto(raiz)
    existentes = set(item["id"] for item in manifesto.get("fontes", []))
    if source_id is None:
        source_id = gerar_source_id(fonte["tipo"], fonte["data"], existentes, fonte["texto"])
    validos = fontes_validas_para_compilar(raiz, source_id)

    erros_anteriores = []
    motivo = "compilacao"

    for tentativa in range(1, max_tentativas + 1):
        prompt = montar_prompt_compilador(
            fonte,
            source_id,
            montar_contexto_numerado(raiz),
            validos,
            erros_anteriores,
        )
        try:
            bruto = chamar_modelo(prompt)
        except ErroApi as erro:
            motivo = "compilacao"
            erros_anteriores = [str(erro)]
            continue

        try:
            dados = _validar_proposta(_carregar_resposta(bruto))
            _validar_textos_gerados(dados, nomes)
            snapshot = _snapshot_wiki(raiz)
            try:
                resultado = aplicar_operacoes(
                    raiz,
                    dados["operacoes"],
                    validos,
                    manifesto=manifesto,
                    fonte=fonte,
                    source_id=source_id,
                    data_documento=fonte["data"],
                    nomes=nomes,
                    contradicoes=dados["contradicoes"],
                )
                erros_lint = _erros_lint(raiz, source_id, lint)
                if erros_lint:
                    raise ErroLint(erros_lint)
            except Exception:
                _restaurar_wiki(raiz, snapshot)
                raise
        except ErroPii as erro:
            motivo = "pii"
            erros_anteriores = [str(erro)]
            continue
        except ErroResposta as erro:
            motivo = "schema"
            erros_anteriores = [str(erro)]
            continue
        except ErroLint as erro:
            motivo = "lint"
            erros_anteriores = erro.erros
            continue
        except ErroOperacao as erro:
            motivo = "compilacao"
            erros_anteriores = [str(erro)]
            continue

        return {
            "source_id": source_id,
            "fonte": fonte,
            "fatos_novos": dados["fatos_novos"],
            "mudancas": dados["mudancas"],
            "contradicoes": dados["contradicoes"],
            "lacunas": dados["lacunas"],
            "operacoes": dados["operacoes"],
            "aplicadas": resultado["aplicadas"],
            "substituicoes": resultado["substituicoes"],
            "pendencias": resultado["pendencias"],
            "novas_paginas": resultado["novas_paginas"],
            "tentativas": tentativa,
        }

    raise ErroCompilacao(motivo, erros_anteriores, max_tentativas)


# ---------------------------------------------------------------------------
# Publicação: fonte derivada, manifesto e PR
# ---------------------------------------------------------------------------

CHECKLIST_GESTAO = (
    "Conferir se o documento é autêntico.",
    "Confirmar a autoridade declarada.",
    "Conferir as citações e a data.",
    "Confirmar que não há dados pessoais.",
    "Conferir as páginas que voltaram a rascunho.",
)


def _como_datetime(agora):
    """Resolve o relógio injetável para um `datetime` UTC."""
    valor = agora() if callable(agora) else agora
    if valor is None:
        return datetime.now(timezone.utc)
    if isinstance(valor, str):
        texto = valor.strip().replace("Z", "+00:00")
        valor = datetime.fromisoformat(texto)
    if not isinstance(valor, datetime):
        raise ErroPublicacao("relógio inválido: use datetime ou texto ISO")
    if valor.tzinfo is None:
        valor = valor.replace(tzinfo=timezone.utc)
    return valor.astimezone(timezone.utc)


def _iso_utc(valor):
    return _como_datetime(valor).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sanitizar_titulo(titulo, nomes=None):
    """Remove nomes do título público, sem caixa e por palavra inteira."""
    texto = str(titulo or "").strip()
    for nome in _nomes_ordenados(nomes):
        padrao = r"(?<!\w)" + re.escape(nome) + r"(?!\w)"
        texto = re.sub(padrao, MARCADOR_NOME, texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto or "Fonte sem título"


def fonte_derivada(texto, nomes=None):
    """Fonte pública: original byte a byte, exceto spans exatos de nome."""
    return substituir_nomes(texto, nomes)


def arquivo_fonte_derivada(source_id, tipo):
    """Caminho relativo da fonte derivada para o tipo informado."""
    if tipo not in PASTA_POR_TIPO:
        raise ErroFonte(f"tipo inválido para fonte derivada: {tipo!r}")
    return f"sources/{PASTA_POR_TIPO[tipo]}/{source_id}.md"


def entrada_manifesto(
    fonte, source_id, hash_conteudo_, submetido_em, *, nomes=None, arquivo=None
):
    """Monta a entrada pública do manifesto, com título sanitizado."""
    tipo = fonte["tipo"]
    if tipo not in AUTORIDADE_POR_TIPO:
        raise ErroFonte(f"tipo inválido para o manifesto: {tipo!r}")
    return {
        "id": source_id,
        "titulo": sanitizar_titulo(fonte["titulo"], nomes),
        "tipo": tipo,
        "autoridade": AUTORIDADE_POR_TIPO[tipo],
        "data": fonte["data"],
        "arquivo": arquivo or arquivo_fonte_derivada(source_id, tipo),
        "uso": "wiki",
        "origem": "envio",
        "submetido_em": submetido_em,
        "hash": hash_conteudo_,
    }


def preparar_publicacao(
    raiz,
    fonte,
    source_id,
    submetido_em,
    *,
    nomes=None,
    texto_derivado=None,
    hash_=None,
    manifesto=None,
):
    """Grava a fonte derivada e acrescenta a entrada ao manifesto.

    A fonte derivada é escrita sem acrescentar ou remover bytes além da
    substituição exata dos nomes. O manifesto só recebe fontes aprovadas;
    quarentena nunca chega aqui.
    """
    raiz = Path(raiz)
    tipo = fonte["tipo"]
    derivado = (
        texto_derivado
        if texto_derivado is not None
        else fonte_derivada(fonte["texto"], nomes)
    )
    arquivo = arquivo_fonte_derivada(source_id, tipo)
    destino = raiz / arquivo
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(derivado.encode("utf-8"))

    if manifesto is None:
        manifesto = carregar_manifesto(raiz)
    fontes = manifesto.setdefault("fontes", [])
    if any(item.get("id") == source_id for item in fontes):
        raise ErroPublicacao(f"source_id já existe no manifesto: {source_id}")
    entrada = entrada_manifesto(
        fonte,
        source_id,
        hash_ if hash_ is not None else hash_conteudo(fonte["texto"]),
        submetido_em,
        nomes=nomes,
        arquivo=arquivo,
    )
    fontes.append(entrada)
    if isinstance(submetido_em, str) and len(submetido_em) >= 10:
        manifesto["atualizado_em"] = submetido_em[:10]
    (raiz / "sources/manifest.json").write_text(
        json.dumps(manifesto, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "source_id": source_id,
        "arquivo": arquivo,
        "entrada": entrada,
        "texto_derivado": derivado,
        "substituicoes": contar_substituicoes(fonte["texto"], nomes or []),
    }


def _texto_seguro(valor, nomes=None):
    """Normaliza um texto para o corpo do PR e remove nomes por segurança."""
    texto = " ".join(str(valor or "").split())
    for nome in _nomes_ordenados(nomes):
        padrao = r"(?<!\w)" + re.escape(nome) + r"(?!\w)"
        texto = re.sub(padrao, MARCADOR_NOME, texto, flags=re.IGNORECASE)
    return texto


def _itens_texto(itens, nomes=None):
    linhas = []
    for item in itens or []:
        if isinstance(item, dict):
            texto = (
                item.get("texto")
                or item.get("resumo")
                or item.get("titulo")
                or ""
            )
            fontes = item.get("fontes") or ([item["fonte"]] if item.get("fonte") else [])
            texto = _texto_seguro(texto, nomes)
            if fontes:
                citacoes = " ".join(
                    f"[fonte: {_texto_seguro(fonte, nomes)}]" for fonte in fontes
                )
                texto = f"{texto} {citacoes}".strip()
        else:
            texto = _texto_seguro(item, nomes)
        if texto:
            linhas.append(texto)
    return linhas


def _resumo_lint(lint=None):
    if lint is None:
        return "não executado"
    if isinstance(lint, dict):
        erros = lint.get("erros", []) or []
        avisos = lint.get("avisos", []) or []
        if not erros:
            return f"0 erros, {len(avisos)} aviso(s)."
        mensagens = []
        for erro in erros:
            if isinstance(erro, dict):
                mensagens.append(
                    str(erro.get("mensagem") or erro.get("detalhe") or erro.get("problema"))
                )
            else:
                mensagens.append(str(erro))
        return f"{len(erros)} erro(s): " + "; ".join(mensagens)
    return str(lint)


def _data_exclusao(submetido_em, exclusao_em=None):
    if exclusao_em:
        return str(exclusao_em)
    try:
        inicio = date.fromisoformat(str(submetido_em)[:10])
    except ValueError:
        return "desconhecida"
    return (inicio + timedelta(days=RETENCAO_BRUTO_DIAS)).isoformat()


def montar_corpo_pr(
    *,
    source_id,
    titulo,
    tipo=None,
    autoridade=None,
    data=None,
    arquivo=None,
    hash_=None,
    submetido_em=None,
    exclusao_em=None,
    nomes_removidos=0,
    classificacao=None,
    lint=None,
    fatos_novos=None,
    mudancas=None,
    contradicoes=None,
    lacunas=None,
    checklist=None,
    possivel_duplicata=False,
    nomes=None,
    fonte=None,
):
    """Monta o corpo público do PR sem UUID, texto bruto ou nomes."""
    if fonte:
        tipo = tipo or fonte.get("tipo")
        data = data or fonte.get("data")
        titulo = titulo or fonte.get("titulo")
    titulo = sanitizar_titulo(titulo, nomes)
    autoridade = autoridade or AUTORIDADE_POR_TIPO.get(tipo, "desconhecida")
    linhas = [
        "## Metadados da fonte",
        "",
        f"- **source_id:** {_texto_seguro(source_id, nomes)}",
        f"- **Título:** {titulo}",
        f"- **Tipo:** {_texto_seguro(tipo or 'desconhecido', nomes)}",
        f"- **Autoridade:** {_texto_seguro(autoridade, nomes)}",
        f"- **Data do documento:** {_texto_seguro(data or 'desconhecida', nomes)}",
        f"- **Arquivo:** {_texto_seguro(arquivo or '', nomes)}",
        f"- **Hash (sha256):** {_texto_seguro(hash_ or '', nomes)}",
        f"- **Submetido em:** {_texto_seguro(submetido_em or 'desconhecido', nomes)}",
        f"- **Exclusão do original:** {_data_exclusao(submetido_em, exclusao_em)} "
        f"(TTL de {RETENCAO_BRUTO_DIAS} dias)",
        "",
        "> **Envio anônimo.** O UUID do job, o texto bruto da fonte e nomes de "
        "pessoas não aparecem neste PR. A gestão revisa antes de publicar.",
        "",
        "## Fatos novos",
        "",
    ]
    fatos = _itens_texto(fatos_novos, nomes)
    linhas.extend([f"- {item}" for item in fatos] or ["- Nenhum fato novo."])
    linhas.extend(["", "## Mudanças", ""])
    mudancas_txt = _itens_texto(mudancas, nomes)
    linhas.extend([f"- {item}" for item in mudancas_txt] or ["- Nenhuma mudança."])
    linhas.extend(["", "## Contradições", ""])
    contras = _itens_texto(contradicoes, nomes)
    linhas.extend([f"- {item}" for item in contras] or ["- Nenhuma contradição."])
    linhas.extend(["", "## Lacunas", ""])
    lacunas_txt = _itens_texto(lacunas, nomes)
    linhas.extend([f"- {item}" for item in lacunas_txt] or ["- Nenhuma lacuna nova."])

    linhas.extend(["", "## Lint", "", f"- {_resumo_lint(lint)}"])

    linhas.extend(["", "## Classificação", ""])
    if isinstance(classificacao, dict):
        injecao = "sim" if classificacao.get("injecao") else "não"
        sinais = classificacao.get("sinais") or []
        linhas.append(
            f"- **Resultado:** {_texto_seguro(classificacao.get('resultado') or 'ok', nomes)}"
        )
        linhas.append(f"- **Injeção detectada:** {injecao}")
        linhas.append(f"- **Sinais:** {len(sinais)}")
    else:
        linhas.append("- Classificação não informada.")

    linhas.extend(
        [
            "",
            "## Nomes removidos",
            "",
            f"- Substituições exatas: {int(nomes_removidos)}",
            "",
            "## Checklist da gestão",
            "",
        ]
    )
    itens_checklist = list(checklist or CHECKLIST_GESTAO)
    if possivel_duplicata:
        itens_checklist.append(
            "Conferir possível duplicata (mesmo tipo, data e título)."
        )
    linhas.extend(f"- [ ] {item}" for item in itens_checklist)
    return "\n".join(linhas).rstrip() + "\n"


def _rodar_comando(comando, raiz):
    processo = subprocess.run(
        comando, cwd=str(raiz), capture_output=True, text=True, check=False
    )
    if processo.returncode != 0:
        detalhe = (processo.stderr or processo.stdout or "").strip()
        raise ErroPublicacao(
            f"{comando[0]} falhou com código {processo.returncode}: {detalhe}"
        )
    return processo.stdout


def _git_padrao(argumentos, raiz):
    return _rodar_comando(["git", *argumentos], raiz)


def _gh_padrao(argumentos, raiz):
    return _rodar_comando(["gh", *argumentos], raiz)


def abrir_pr(
    raiz,
    source_id,
    titulo,
    corpo,
    *,
    labels=None,
    nomes=None,
    git=None,
    gh=None,
):
    """Cria branch, um único commit e abre o PR com efeitos injetáveis."""
    raiz = Path(raiz)
    git = git or _git_padrao
    gh = gh or _gh_padrao
    branch = f"ingest/{source_id}"
    mensagem = f"Ingest {source_id}"
    titulo_fonte = sanitizar_titulo(titulo, nomes)
    titulo_pr = f"Ingest {source_id}: {titulo_fonte}"
    rotulos = list(labels or ["ingest", "precisa-verificacao"])

    git(["checkout", "-B", branch], raiz)
    git(["add", "--all"], raiz)
    git(["commit", "-m", mensagem], raiz)
    argumentos_gh = [
        "pr",
        "create",
        "--title",
        titulo_pr,
        "--body",
        corpo,
        "--base",
        "main",
        "--head",
        branch,
    ]
    for rotulo in rotulos:
        argumentos_gh.extend(["--label", rotulo])
    saida = gh(argumentos_gh, raiz)
    url = next(
        (linha.strip() for linha in str(saida).splitlines() if linha.strip().startswith("http")),
        str(saida).strip(),
    )
    return {
        "branch": branch,
        "commit": mensagem,
        "titulo": titulo_pr,
        "corpo": corpo,
        "labels": rotulos,
        "url": url,
    }


def _ids_existentes(raiz, existentes=None):
    ids = {item["id"] for item in carregar_manifesto(raiz).get("fontes", [])}
    for item in existentes or []:
        if isinstance(item, str):
            ids.add(item)
        elif isinstance(item, dict):
            if item.get("source_id"):
                ids.add(str(item["source_id"]))
            elif item.get("id") and not re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                str(item["id"]),
            ):
                ids.add(str(item["id"]))
    return ids


def _emitir_resultado(
    status,
    motivo,
    source_id,
    *,
    atualizar_status=None,
    notificar=None,
    pr_url=None,
    evento=None,
):
    if atualizar_status is not None:
        atualizar_status(status, motivo)
    if notificar is not None:
        payload = {
            "evento": evento or ("pr_aberto" if status == "aguardando_revisao" else status),
            "source_id": source_id,
            "status": status,
            "motivo": motivo,
        }
        if pr_url:
            payload["pr_url"] = pr_url
        notificar(payload)


def _restaurar_publicacao(raiz, snapshot_wiki, manifesto_original, derivado):
    _restaurar_wiki(raiz, snapshot_wiki)
    (Path(raiz) / "sources/manifest.json").write_text(
        manifesto_original, encoding="utf-8"
    )
    caminho = Path(raiz) / derivado
    if caminho.exists():
        caminho.unlink()


def publicar(
    fonte,
    raiz,
    chamar_modelo,
    *,
    resultado_triagem=None,
    nomes=None,
    texto_derivado=None,
    source_id=None,
    existentes=None,
    submetido_em=None,
    agora=None,
    lint=None,
    git=None,
    gh=None,
    atualizar_status=None,
    notificar=None,
    max_tentativas=MAX_TENTATIVAS,
    possivel_duplicata=False,
):
    """Compila, grava a fonte derivada, roda o lint e abre o PR.

    A triagem é opcional: quando `resultado_triagem` chega em `quarentena` ou
    `falhou`, nada é publicado. `sem_mudancas` não grava fonte derivada, não
    entra no manifesto e não abre PR. Todos os efeitos externos são
    injetáveis (`lint`, `git`, `gh`, relógio e notificação).
    """
    raiz = Path(raiz)
    resultado_triagem = resultado_triagem or {}
    nomes = list(nomes if nomes is not None else resultado_triagem.get("nomes") or [])
    if isinstance(existentes, dict):
        existentes = list(existentes.values())
    submetido_em = submetido_em or _iso_utc(agora)

    status_triagem = resultado_triagem.get("resultado")
    if status_triagem in ("quarentena", "falhou"):
        motivo = resultado_triagem.get("motivo") or status_triagem
        _emitir_resultado(
            status_triagem,
            motivo,
            resultado_triagem.get("source_id"),
            atualizar_status=atualizar_status,
            notificar=notificar,
            evento=status_triagem,
        )
        return {
            "status": status_triagem,
            "resultado": status_triagem,
            "motivo": motivo,
            "source_id": resultado_triagem.get("source_id"),
            "pr": None,
        }

    fonte = validar_fonte(fonte)
    ids = _ids_existentes(raiz, existentes)
    if source_id is None:
        source_id = gerar_source_id(
            fonte["tipo"], fonte["data"], ids, fonte["texto"], sufixo_sempre=True
        )
    original_derivado = (
        texto_derivado
        if texto_derivado is not None
        else resultado_triagem.get("texto_derivado")
        or fonte_derivada(fonte["texto"], nomes)
    )
    hash_ = hash_conteudo(fonte["texto"])
    snapshot_wiki = _snapshot_wiki(raiz)
    manifesto_original = (raiz / "sources/manifest.json").read_text(encoding="utf-8")

    try:
        compilacao = compilar(
            fonte,
            raiz,
            chamar_modelo,
            source_id=source_id,
            nomes=nomes,
            max_tentativas=max_tentativas,
            lint=lint,
        )
    except ErroCompilacao as erro:
        _emitir_resultado(
            "falhou",
            erro.motivo,
            source_id,
            atualizar_status=atualizar_status,
            notificar=notificar,
            evento="falhou",
        )
        return {
            "status": "falhou",
            "resultado": "falhou",
            "motivo": erro.motivo,
            "source_id": source_id,
            "erros": list(erro.erros),
            "pr": None,
        }

    if not compilacao["operacoes"] and not compilacao["contradicoes"]:
        _emitir_resultado(
            "sem_mudancas",
            None,
            source_id,
            atualizar_status=atualizar_status,
            notificar=notificar,
            evento="sem_mudancas",
        )
        return {
            "status": "sem_mudancas",
            "resultado": "sem_mudancas",
            "motivo": None,
            "source_id": source_id,
            "pr": None,
            "compilacao": compilacao,
        }

    publicacao = preparar_publicacao(
        raiz,
        fonte,
        source_id,
        submetido_em,
        nomes=nomes,
        texto_derivado=original_derivado,
        hash_=hash_,
    )
    if lint is None:
        from tools.lint_wiki import revisar

        lint = revisar
    resultado_lint = _normalizar_lint(lint(raiz))
    erros_lint = _erros_de_lint(resultado_lint, source_id)
    if erros_lint:
        _restaurar_publicacao(
            raiz, snapshot_wiki, manifesto_original, publicacao["arquivo"]
        )
        _emitir_resultado(
            "falhou",
            "lint",
            source_id,
            atualizar_status=atualizar_status,
            notificar=notificar,
            evento="falhou",
        )
        return {
            "status": "falhou",
            "resultado": "falhou",
            "motivo": "lint",
            "source_id": source_id,
            "erros": erros_lint,
            "pr": None,
        }

    nomes_removidos = contar_substituicoes(fonte["texto"], nomes)
    corpo = montar_corpo_pr(
        source_id=source_id,
        titulo=fonte["titulo"],
        tipo=fonte["tipo"],
        autoridade=AUTORIDADE_POR_TIPO[fonte["tipo"]],
        data=fonte["data"],
        arquivo=publicacao["arquivo"],
        hash_=hash_,
        submetido_em=submetido_em,
        nomes_removidos=nomes_removidos,
        classificacao=resultado_triagem,
        lint=resultado_lint,
        fatos_novos=compilacao["fatos_novos"],
        mudancas=compilacao["mudancas"],
        contradicoes=compilacao["contradicoes"],
        lacunas=compilacao["lacunas"],
        possivel_duplicata=possivel_duplicata,
        nomes=nomes,
    )
    rotulos = ["ingest", "precisa-verificacao"]
    if possivel_duplicata:
        rotulos.append("possivel-duplicata")
    try:
        pr = abrir_pr(
            raiz,
            source_id,
            fonte["titulo"],
            corpo,
            labels=rotulos,
            nomes=nomes,
            git=git,
            gh=gh,
        )
    except ErroPublicacao as erro:
        _emitir_resultado(
            "falhou",
            "infra",
            source_id,
            atualizar_status=atualizar_status,
            notificar=notificar,
            evento="falhou",
        )
        return {
            "status": "falhou",
            "resultado": "falhou",
            "motivo": "infra",
            "source_id": source_id,
            "erros": [str(erro)],
            "pr": None,
        }

    _emitir_resultado(
        "aguardando_revisao",
        None,
        source_id,
        atualizar_status=atualizar_status,
        notificar=notificar,
        pr_url=pr.get("url"),
        evento="pr_aberto",
    )
    return {
        "status": "aguardando_revisao",
        "resultado": "aguardando_revisao",
        "motivo": None,
        "source_id": source_id,
        "pr": pr,
        "publicacao": publicacao,
        "compilacao": compilacao,
        "nomes_removidos": nomes_removidos,
        "lint": resultado_lint,
    }


def _transporte_urllib(url, cabecalhos, corpo):
    pedido = urllib.request.Request(
        url, data=json.dumps(corpo).encode("utf-8"), headers=cabecalhos, method="POST"
    )
    with urllib.request.urlopen(pedido, timeout=120) as resposta:
        return json.load(resposta)


def chamar_gemini_json(
    prompt,
    modelo=None,
    chave=None,
    response_schema=None,
    transporte=None,
    tentativas=MAX_TENTATIVAS,
):
    """Chama o Gemini exigindo `responseMimeType=application/json` e schema."""
    modelo = modelo or MODELO_INGEST
    if chave is None:
        chave = os.environ.get("GEMINI_API_KEY_INGEST") or os.environ.get("GEMINI_API_KEY") or ""
    if not chave:
        raise ErroApi("chave do Gemini ausente; defina GEMINI_API_KEY_INGEST ou GEMINI_API_KEY")

    url = f"{BASE_URL}/models/{modelo}:generateContent"
    cabecalhos = {"Content-Type": "application/json", "x-goog-api-key": chave}
    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema or ESQUEMA_RESPOSTA,
        },
    }
    transporte = transporte or _transporte_urllib

    for tentativa in range(1, tentativas + 1):
        try:
            dados = transporte(url, cabecalhos, corpo)
            if isinstance(dados, (bytes, str)):
                dados = json.loads(dados)
            partes = dados["candidates"][0]["content"]["parts"]
            texto = "".join(p.get("text", "") for p in partes if not p.get("thought"))
            if not texto.strip():
                raise ErroApi("resposta vazia")
            return texto.strip()
        except ErroApi:
            raise
        except urllib.error.HTTPError as erro:
            if erro.code not in (429, 500, 502, 503, 504) or tentativa == tentativas:
                raise ErroApi(f"HTTP {erro.code} ao chamar o Gemini") from erro
            espera = int(erro.headers.get("Retry-After") or 0) or 15 * tentativa
            time.sleep(espera)
        except (urllib.error.URLError, TimeoutError) as erro:
            if tentativa == tentativas:
                raise ErroApi(f"falha de conexão com o Gemini: {erro}") from erro
            time.sleep(10 * tentativa)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as erro:
            raise ErroApi(f"formato inesperado da resposta do Gemini: {erro}") from erro
    raise ErroApi("tentativas esgotadas")
