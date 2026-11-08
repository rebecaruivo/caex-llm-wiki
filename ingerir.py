"""Núcleo puro do compilador de ingestão.

Esta fatia aplica apenas `adicionar_linha`. Funções de triagem, publicação e
guardrails completos chegam em outros tickets; aqui ficam normalização, hash,
`source_id`, PII, contexto numerado, contrato JSON e aplicação de operações.
"""
import hashlib
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from config_ingestao import (  # noqa: F401  (re-exportado para consumidores)
    DOMINIO_INSTITUCIONAL,
    MAX_BYTES,
    MAX_TENTATIVAS,
    MODELO_INGEST,
    PII_REGEX,
    PREFIXO_POR_TIPO,
    TIPOS,
)

ROOT = Path(__file__).resolve().parent
BASE_URL = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
CITACAO = re.compile(r"\[fonte: ([A-Z0-9-]+)\]")
FONTES_VALIDAS_PLACEHOLDER = "{{fontes_validas}}"


class ErroApi(Exception):
    """Falha ao falar com o Gemini."""


class ErroFonte(Exception):
    """Fonte local inválida (tipo, data, tamanho, PII)."""


class ErroResposta(Exception):
    """Resposta do modelo fora do contrato JSON."""


class ErroOperacao(Exception):
    """Operação recusada pelo código antes de tocar nos arquivos."""


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


def gerar_source_id(tipo, data, existentes, texto=""):
    """Gera `PREFIXO-DATA`, acrescentando sufixo hex 6→8→10 em colisão."""
    if tipo not in PREFIXO_POR_TIPO:
        raise ErroFonte(f"tipo inválido para source_id: {tipo!r}")
    base = f"{PREFIXO_POR_TIPO[tipo]}-{data}"
    if base not in existentes:
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


def montar_prompt_compilador(fonte, source_id, contexto, fontes_validas):
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
    return template.replace("{{fonte_texto}}", fonte["texto"])


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


def _normalizar_secao(secao):
    return str(secao or "").strip().removeprefix("## ").strip()


def _formatar_linha(texto):
    texto = str(texto).strip()
    if not texto:
        raise ErroOperacao("operação adicionar_linha com texto vazio")
    if texto.startswith("- ") or re.match(r"^\d+\. ", texto):
        return texto
    return f"- {texto}"


def _validar_citacoes(texto, fontes_validas):
    citadas = CITACAO.findall(texto)
    if not citadas:
        raise ErroOperacao("linha sem citação [fonte: ID]")
    desconhecidas = sorted(set(citadas) - set(fontes_validas))
    if desconhecidas:
        raise ErroOperacao(f"citação de fonte desconhecida: {', '.join(desconhecidas)}")
    return citadas


def _indice_da_secao(linhas, secao):
    alvo = f"## {secao}"
    for indice, linha in enumerate(linhas):
        if linha.strip() == alvo:
            return indice
    raise ErroOperacao(f"seção não encontrada: {secao!r}")


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


def aplicar_operacoes(raiz, operacoes, fontes_validas):
    """Aplica operações `adicionar_linha` de forma atômica (só grava no fim)."""
    if not isinstance(operacoes, list):
        raise ErroOperacao("'operacoes' precisa ser uma lista")

    raiz = Path(raiz).resolve()
    paginas = {}
    alteradas = []
    aplicadas = []

    for operacao in operacoes:
        if not isinstance(operacao, dict):
            raise ErroOperacao("operação precisa ser um objeto JSON")
        tipo = operacao.get("tipo")
        if tipo != "adicionar_linha":
            raise ErroOperacao(
                f"operação não suportada nesta fatia: {tipo!r}; "
                "apenas 'adicionar_linha' é aceita"
            )

        caminho = _caminho_da_pagina(raiz, operacao.get("pagina"))
        if caminho not in paginas:
            paginas[caminho] = caminho.read_text(encoding="utf-8").splitlines()

        texto = operacao.get("texto")
        _validar_citacoes(str(texto or ""), fontes_validas)
        nova_linha = _formatar_linha(texto)
        linhas = paginas[caminho]

        linha = operacao.get("linha")
        hash_referencia = operacao.get("hash")
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
            linhas.insert(linha, nova_linha)
        else:
            if hash_referencia:
                raise ErroOperacao("'hash' só é aceito junto de 'linha'")
            _inserir_na_secao(linhas, _normalizar_secao(operacao.get("secao")), nova_linha)

        if caminho not in alteradas:
            alteradas.append(caminho)
        aplicadas.append({"pagina": caminho.relative_to(raiz).as_posix(), "texto": nova_linha})

    for caminho in alteradas:
        _sincronizar_fontes(paginas[caminho])
        caminho.write_text("\n".join(paginas[caminho]) + "\n", encoding="utf-8")

    return {"aplicadas": aplicadas}


def compilar(fonte, raiz, chamar_modelo, *, source_id=None):
    """Compila uma fonte local: prompt numerado, modelo JSON, operações, resultado."""
    fonte = validar_fonte(fonte)
    raiz = Path(raiz)
    existentes = set(ids_manifesto(raiz))
    if source_id is None:
        source_id = gerar_source_id(fonte["tipo"], fonte["data"], existentes, fonte["texto"])
    validos = fontes_validas_para_compilar(raiz, source_id)

    prompt = montar_prompt_compilador(
        fonte, source_id, montar_contexto_numerado(raiz), validos
    )
    dados = _carregar_resposta(chamar_modelo(prompt))
    operacoes = dados.get("operacoes", [])
    if not isinstance(operacoes, list):
        raise ErroResposta("'operacoes' precisa ser uma lista")

    resultado = aplicar_operacoes(raiz, operacoes, validos)
    return {
        "source_id": source_id,
        "fonte": fonte,
        "fatos_novos": dados.get("fatos_novos", []),
        "mudancas": dados.get("mudancas", []),
        "contradicoes": dados.get("contradicoes", []),
        "lacunas": dados.get("lacunas", []),
        "operacoes": operacoes,
        "aplicadas": resultado["aplicadas"],
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
