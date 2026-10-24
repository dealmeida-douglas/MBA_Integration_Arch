"""
Servidor MCP (Model Context Protocol) do Quantum Commerce.

O que é um servidor MCP?
------------------------
MCP é um protocolo que permite a um cliente de IA (Cursor, Claude Desktop etc.)
descobrir e chamar *Tools* (funções) do seu processo. O cliente não lê o Excel
diretamente: ele pede ao servidor, e o servidor devolve JSON estruturado.

Como o processo se comunica?
----------------------------
Por padrão o transporte é STDIO: o host inicia este arquivo como subprocesso,
escreve pedidos no stdin e lê respostas no stdout. Por isso não usamos print()
para logs (corromperia o protocolo). Logging vai para stderr.

Como registrar uma Tool?
------------------------
O decorator @mcp.tool() expõe a função Python. O docstring vira a descrição
que o modelo lê para decidir quando chamar a Tool. Type hints viram o schema
dos parâmetros.

Como registrar uma Resource?
----------------------------
O decorator @mcp.resource("uri") expõe um documento só de leitura, identificado
por uma URI. O cliente lista (resources/list) e lê (resources/read). Não há
parâmetro de busca: o conteúdo já é o catálogo completo.

Como registrar um Prompt?
-------------------------
O decorator @mcp.prompt() expõe um *template* de conversa. Diferente da Tool
(que executa código e devolve dado) e da Resource (que entrega um documento),
o Prompt devolve o texto de instrução que o cliente coloca no modelo. Serve
para padronizar o jeito de usar as Tools e a Resource neste servidor.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# Garante que "from mcp_server ..." funcione mesmo se o host executar
# mcp_server/server.py em vez de python -m mcp_server.server.
_RAIZ_PROJETO = Path(__file__).resolve().parent.parent
if str(_RAIZ_PROJETO) not in sys.path:
    sys.path.insert(0, str(_RAIZ_PROJETO))

from mcp.server.fastmcp import FastMCP

from mcp_server.data_loader import (
    buscar_produto_por_nome as consultar_por_nome,
    exportar_catalogo_completo,
    listar_produtos_por_categoria as consultar_catalogo,
    obter_avaliacao_produto as consultar_avaliacao,
    obter_produto_por_id as consultar_por_id,
)

# Logs no stderr: o stdout é o "fio" do protocolo MCP.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("quantum-commerce-mcp")

# Nome visível para o cliente MCP (aparece na UI do host).
mcp = FastMCP("quantum-commerce")


@mcp.tool()
def listar_produtos_por_categoria(categoria: str = "") -> dict:
    """Lista os produtos do catálogo Quantum Commerce agrupados por categoria.

    Use esta Tool quando o usuário quiser ver o mix de produtos, filtrar por
    categoria (brinquedo, game, livro, maquiagem, outros) ou conferir preço
    médio e avaliação média. Os dados vêm da planilha de chamados em db_csv/;
    produtos repetidos em várias reclamações aparecem uma única vez.

    Args:
        categoria: Categoria desejada (ex.: "game"). Deixe vazio para listar
            todas as categorias de uma vez. A comparação ignora maiúsculas.

    Returns:
        Dicionário com ok, totais, categorias_disponiveis e
        produtos_por_categoria (id, nome, descrição, preco_medio, avaliacao_media).
    """
    filtro = categoria.strip() or None
    logger.info("Tool listar_produtos_por_categoria chamada (categoria=%r)", filtro)
    return consultar_catalogo(filtro)


@mcp.tool()
def obter_produto_por_id(id_produto: str) -> dict:
    """Busca um produto pelo ID e devolve nome, categoria e valor.

    Use esta Tool quando o usuário já souber o código do produto (P001 a P033)
    e quiser os dados de catálogo: nome, categoria e preço.

    Args:
        id_produto: Identificador do produto, por exemplo "P009".
            Maiúsculas e minúsculas são ignoradas.

    Returns:
        Dicionário com ok e produto (id_produto, nome, categoria, valor, descricao).
    """
    logger.info("Tool obter_produto_por_id chamada (id_produto=%r)", id_produto)
    return consultar_por_id(id_produto)


@mcp.tool()
def buscar_produto_por_nome(nome: str) -> dict:
    """Busca produtos cujo nome contém o texto informado.

    Use esta Tool quando o usuário lembrar só de parte do nome, como
    "headset", "batom" ou "mochila". A busca ignora maiúsculas e acentos.

    Args:
        nome: Trecho do nome do produto (não precisa ser o nome completo).

    Returns:
        Dicionário com ok, total e a lista de produtos encontrados.
    """
    logger.info("Tool buscar_produto_por_nome chamada (nome=%r)", nome)
    return consultar_por_nome(nome)


@mcp.tool()
def obter_avaliacao_produto(produto: str) -> dict:
    """Devolve a nota média de avaliação/review de um produto.

    Use esta Tool quando o usuário perguntar a nota, o review ou a avaliação
    de um item. Informe o ID (P009) ou um nome que identifique um único produto.
    A nota é a média da coluna avaliacao_produto nos chamados daquele item.

    Args:
        produto: ID (ex.: "P009") ou nome (ex.: "Headset gamer HX-70").

    Returns:
        Dicionário com ok e avaliacao (nota de 0 a 5, quantidade de avaliações
        e um resumo em texto).
    """
    logger.info("Tool obter_avaliacao_produto chamada (produto=%r)", produto)
    return consultar_avaliacao(produto)


@mcp.resource("catalogo://produtos")
def catalogo_produtos() -> str:
    """Catálogo completo de produtos do Quantum Commerce (somente leitura).

    Use esta Resource quando precisar de todos os produtos de uma vez, sem
    filtro. O conteúdo já vem pronto: produtos únicos extraídos da planilha
    de chamados, em lista plana e agrupados por categoria.
    """
    logger.info("Resource catalogo://produtos lida")
    return json.dumps(exportar_catalogo_completo(), ensure_ascii=False, indent=2)


@mcp.prompt()
def assistente_catalogo(pergunta: str = "") -> str:
    """Prompt padrão do assistente de catálogo Quantum Commerce.

    Use este Prompt no início do trabalho: ele instrui o modelo a consultar
    a Resource e as Tools deste servidor em vez de inventar produtos, preços
    ou avaliações. Opcionalmente informe a pergunta do usuário.
    """
    logger.info("Prompt assistente_catalogo solicitado (pergunta=%r)", pergunta)
    extra = ""
    if pergunta.strip():
        extra = f"\nPergunta do usuário:\n{pergunta.strip()}\n"

    return f"""Você é o assistente de catálogo da Quantum Commerce.

Nunca invente produto, preço, categoria ou avaliação. Toda resposta deve
vir das primitivas MCP deste servidor.

Como consultar os dados:
1. Visão geral do mix: leia a Resource catalogo://produtos.
2. Filtro por categoria (brinquedo, game, livro, maquiagem, outros):
   chame a Tool listar_produtos_por_categoria.
3. Código conhecido (P001 a P033): chame a Tool obter_produto_por_id.
4. Nome incompleto (ex.: headset, batom): chame a Tool buscar_produto_por_nome.
5. Nota ou review: chame a Tool obter_avaliacao_produto.

Responda em português. Cite id_produto, nome, categoria, preço e avaliação
quando existirem. Se um item não estiver no catálogo, diga isso com clareza.
{extra}"""


def main() -> None:
    """Sobe o servidor no transporte padrão (stdio) e fica aguardando o host."""
    logger.info("Iniciando servidor MCP quantum-commerce (stdio)")
    mcp.run()


if __name__ == "__main__":
    # Evita que "import server" (Inspector / testes) já suba o loop de STDIO.
    # Só executamos mcp.run() quando este arquivo é o ponto de entrada.
    main()
