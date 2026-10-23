"""
Camada de dados do servidor MCP.

O arquivo Excel em db_csv/ não é um catálogo de produtos: cada linha é um
chamado (reclamação). Várias reclamações podem citar o mesmo produto.
Por isso extraímos um catálogo único pela coluna id_produto antes de
expor a Tool de listagem por categoria.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

import pandas as pd

# Caminho absoluto até o .xlsx, independente de onde o processo é iniciado.
# data_loader.py -> mcp_server/ -> raiz do projeto -> db_csv/
CAMINHO_EXCEL = (
    Path(__file__).resolve().parent.parent
    / "db_csv"
    / "quantum-commerce-chamados.xlsx"
)

# Nome da aba no workbook (inspecionado no arquivo: existe apenas a aba "in").
NOME_ABA = "in"

# Colunas de produto usadas pela Tool. As demais (texto da reclamação,
# cliente, tags etc.) ficam de fora desta primeira Tool de catálogo.
COLUNAS_PRODUTO = [
    "id_produto",
    "nome_produto",
    "descricao_produto",
    "categoria_produto",
    "preco",
    "avaliacao_produto",
]


def _normalizar_texto(valor: str) -> str:
    """Compara textos sem maiúsculas, espaços extras nem acentos."""
    sem_acento = "".join(
        ch
        for ch in unicodedata.normalize("NFKD", str(valor).strip().lower())
        if not unicodedata.combining(ch)
    )
    return " ".join(sem_acento.split())


def _normalizar_categoria(valor: str) -> str:
    """Compara categorias sem se preocupar com maiúsculas ou espaços extras."""
    return _normalizar_texto(valor)


def _normalizar_id(valor: str) -> str:
    """IDs como P001 e p001 devem ser tratados como o mesmo produto."""
    return str(valor).strip().upper()


def _consertar_texto(valor):
    """
    Corrige textos da planilha gravados como UTF-8 lido em Latin-1.

    Exemplo: 'AmÃ©lia' (dois caracteres Ã + ©) vira 'Amélia'.
    Se o texto já estiver correto, devolvemos o original.
    """
    if not isinstance(valor, str):
        return valor
    try:
        return valor.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return valor


@lru_cache(maxsize=1)
def carregar_catalogo() -> pd.DataFrame:
    """
    Lê o Excel uma única vez (cache em memória) e devolve o catálogo.

    Passos:
    1. Validar se o arquivo existe (erro claro se o .xlsx não estiver no repo).
    2. Ler a aba "in" com pandas + openpyxl.
    3. Garantir que as colunas esperadas existem.
    4. Deduplicar por id_produto: um produto, uma linha.
    """
    if not CAMINHO_EXCEL.exists():
        raise FileNotFoundError(
            f"Planilha não encontrada em {CAMINHO_EXCEL}. "
            "Confira se o arquivo .xlsx esta na pasta db_csv/."
        )

    # engine="openpyxl" é o leitor de .xlsx.
    chamados = pd.read_excel(
        CAMINHO_EXCEL,
        sheet_name=NOME_ABA,
        engine="openpyxl",
    )

    colunas_faltando = [c for c in COLUNAS_PRODUTO if c not in chamados.columns]
    if colunas_faltando:
        raise ValueError(
            "A planilha não tem as colunas esperadas: "
            f"{colunas_faltando}. Colunas encontradas: {list(chamados.columns)}"
        )

    catalogo = (
        chamados[COLUNAS_PRODUTO]
        .dropna(subset=["id_produto", "categoria_produto"])
        .copy()
    )

    # Textos (nome, descrição, categoria, ids) passam pelo conserto de encoding.
    for coluna in catalogo.select_dtypes(include=["object"]).columns:
        catalogo[coluna] = catalogo[coluna].map(_consertar_texto)

    # Um chamado não define o catálogo: agrupamos pelo ID do produto.
    # - nome/descrição/categoria: primeira ocorrência (são estáveis).
    # - preço / avaliação: média, porque variam de um chamado para outro.
    catalogo["preco"] = pd.to_numeric(catalogo["preco"], errors="coerce")
    catalogo["avaliacao_produto"] = pd.to_numeric(
        catalogo["avaliacao_produto"], errors="coerce"
    )

    catalogo = (
        catalogo.groupby("id_produto", as_index=False)
        .agg(
            nome_produto=("nome_produto", "first"),
            descricao_produto=("descricao_produto", "first"),
            categoria_produto=("categoria_produto", "first"),
            preco_medio=("preco", "mean"),
            avaliacao_media=("avaliacao_produto", "mean"),
            quantidade_avaliacoes=("avaliacao_produto", "count"),
        )
        .sort_values(["categoria_produto", "id_produto"])
        .reset_index(drop=True)
    )

    # Arredonda para leitura humana na resposta da Tool.
    catalogo["preco_medio"] = catalogo["preco_medio"].round(2)
    catalogo["avaliacao_media"] = catalogo["avaliacao_media"].round(1)
    catalogo["quantidade_avaliacoes"] = catalogo["quantidade_avaliacoes"].astype(int)
    return catalogo


def listar_categorias() -> list[str]:
    """Lista as categorias distintas, em ordem alfabética."""
    catalogo = carregar_catalogo()
    return sorted(catalogo["categoria_produto"].dropna().unique().tolist())


def listar_produtos_por_categoria(categoria: str | None = None) -> dict:
    """
    Agrupa os produtos por categoria_produto.

    categoria:
      - None ou string vazia -> devolve todas as categorias.
      - valor informado -> filtra (case-insensitive). Se nao existir,
        devolve um aviso com as categorias validas.
    """
    catalogo = carregar_catalogo()
    categorias_validas = listar_categorias()

    if categoria and categoria.strip():
        chave = _normalizar_categoria(categoria)
        mapa = {_normalizar_categoria(c): c for c in categorias_validas}
        if chave not in mapa:
            return {
                "ok": False,
                "mensagem": (
                    f"Categoria '{categoria}' não encontrada. "
                    f"Use uma destas: {', '.join(categorias_validas)}"
                ),
                "categorias_disponiveis": categorias_validas,
                "produtos_por_categoria": {},
            }
        catalogo = catalogo[
            catalogo["categoria_produto"].map(_normalizar_categoria) == chave
        ]

    agrupado: dict[str, list[dict]] = {}
    for nome_categoria, grupo in catalogo.groupby("categoria_produto", sort=True):
        agrupado[str(nome_categoria)] = grupo.drop(
            columns=["categoria_produto"]
        ).to_dict(orient="records")

    total = int(catalogo.shape[0])
    return {
        "ok": True,
        "total_produtos": total,
        "total_categorias": len(agrupado),
        "categorias_disponiveis": categorias_validas,
        "produtos_por_categoria": agrupado,
    }


def _linha_produto(linha: pd.Series) -> dict:
    """Converte uma linha do catálogo no dicionário padrão das Tools."""
    return {
        "id_produto": linha["id_produto"],
        "nome_produto": linha["nome_produto"],
        "descricao_produto": linha["descricao_produto"],
        "categoria_produto": linha["categoria_produto"],
        "preco": float(linha["preco_medio"]) if pd.notna(linha["preco_medio"]) else None,
        "avaliacao_media": (
            float(linha["avaliacao_media"]) if pd.notna(linha["avaliacao_media"]) else None
        ),
        "quantidade_avaliacoes": int(linha["quantidade_avaliacoes"]),
    }


def obter_produto_por_id(id_produto: str) -> dict:
    """
    Localiza um produto pelo ID (ex.: P009).

    Devolve nome, categoria e valor (preço médio nos chamados).
    """
    chave = _normalizar_id(id_produto)
    if not chave:
        return {
            "ok": False,
            "mensagem": "Informe um id_produto, por exemplo P009.",
            "produto": None,
        }

    catalogo = carregar_catalogo()
    encontrados = catalogo[catalogo["id_produto"].map(_normalizar_id) == chave]
    if encontrados.empty:
        exemplos = ", ".join(catalogo["id_produto"].head(5).tolist())
        return {
            "ok": False,
            "mensagem": (
                f"Produto '{id_produto}' não encontrado. "
                f"Exemplos de IDs válidos: {exemplos}."
            ),
            "produto": None,
        }

    linha = encontrados.iloc[0]
    dados = _linha_produto(linha)
    return {
        "ok": True,
        "produto": {
            "id_produto": dados["id_produto"],
            "nome": dados["nome_produto"],
            "categoria": dados["categoria_produto"],
            "valor": dados["preco"],
            "descricao": dados["descricao_produto"],
        },
    }


def buscar_produto_por_nome(nome: str) -> dict:
    """
    Busca produtos cujo nome contém o texto informado.

    A comparação ignora maiúsculas e acentos: "cafe" encontra "Cafeteira".
    """
    termo = _normalizar_texto(nome)
    if not termo:
        return {
            "ok": False,
            "mensagem": "Informe um trecho do nome do produto, por exemplo 'headset'.",
            "total": 0,
            "produtos": [],
        }

    catalogo = carregar_catalogo()
    nomes = catalogo["nome_produto"].map(_normalizar_texto)
    encontrados = catalogo[nomes.str.contains(termo, regex=False)]

    produtos = [_linha_produto(linha) for _, linha in encontrados.iterrows()]
    if not produtos:
        return {
            "ok": False,
            "mensagem": (
                f"Nenhum produto encontrado para '{nome}'. "
                "Tente um trecho menor, como 'mochila' ou 'batom'."
            ),
            "total": 0,
            "produtos": [],
        }

    return {
        "ok": True,
        "total": len(produtos),
        "termo_buscado": nome.strip(),
        "produtos": produtos,
    }


def _resolver_produto(produto: str) -> pd.Series | None:
    """Aceita ID (P009) ou nome (exato / contém um único resultado)."""
    catalogo = carregar_catalogo()
    chave_id = _normalizar_id(produto)
    por_id = catalogo[catalogo["id_produto"].map(_normalizar_id) == chave_id]
    if not por_id.empty:
        return por_id.iloc[0]

    termo = _normalizar_texto(produto)
    if not termo:
        return None
    nomes = catalogo["nome_produto"].map(_normalizar_texto)
    exatos = catalogo[nomes == termo]
    if len(exatos) == 1:
        return exatos.iloc[0]
    parciais = catalogo[nomes.str.contains(termo, regex=False)]
    if len(parciais) == 1:
        return parciais.iloc[0]
    return None


def obter_avaliacao_produto(produto: str) -> dict:
    """
    Devolve a nota média de avaliação de um produto.

    produto pode ser o ID (P009) ou um nome que identifique um único item.
    A nota é a média da coluna avaliacao_produto nos chamados daquele produto.
    """
    if not str(produto).strip():
        return {
            "ok": False,
            "mensagem": "Informe o produto pelo ID (P009) ou pelo nome.",
            "avaliacao": None,
        }

    linha = _resolver_produto(produto)
    if linha is None:
        candidatos = buscar_produto_por_nome(produto)
        if candidatos.get("ok") and candidatos["total"] > 1:
            nomes = [
                f"{p['id_produto']} ({p['nome_produto']})"
                for p in candidatos["produtos"]
            ]
            return {
                "ok": False,
                "mensagem": (
                    f"'{produto}' encontrou {candidatos['total']} produtos. "
                    f"Seja mais específico ou use o ID: {', '.join(nomes)}."
                ),
                "avaliacao": None,
            }
        return {
            "ok": False,
            "mensagem": (
                f"Produto '{produto}' não encontrado. "
                "Use um ID como P009 ou um nome como 'Headset gamer'."
            ),
            "avaliacao": None,
        }

    dados = _linha_produto(linha)
    nota = dados["avaliacao_media"]
    return {
        "ok": True,
        "avaliacao": {
            "id_produto": dados["id_produto"],
            "nome": dados["nome_produto"],
            "nota": nota,
            "escala": "0 a 5",
            "quantidade_avaliacoes": dados["quantidade_avaliacoes"],
            "resumo": (
                f"{dados['nome_produto']} tem nota {nota}/5 "
                f"com base em {dados['quantidade_avaliacoes']} avaliações."
            ),
        },
    }
