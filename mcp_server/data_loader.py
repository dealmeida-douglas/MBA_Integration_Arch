"""
Camada de dados do servidor MCP.

O arquivo Excel em db_csv/ não é um catálogo de produtos: cada linha é um
chamado (reclamação). Várias reclamações podem citar o mesmo produto.
Por isso extraímos um catálogo único pela coluna id_produto antes de
expor a Tool de listagem por categoria.
"""

from __future__ import annotations

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


def _normalizar_categoria(valor: str) -> str:
    """Compara categorias sem se preocupar com maiúsculas ou espaços extras."""
    return str(valor).strip().lower()


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
        )
        .sort_values(["categoria_produto", "id_produto"])
        .reset_index(drop=True)
    )

    # Arredonda para leitura humana na resposta da Tool.
    catalogo["preco_medio"] = catalogo["preco_medio"].round(2)
    catalogo["avaliacao_media"] = catalogo["avaliacao_media"].round(1)
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
