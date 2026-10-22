# Servidor MCP — Quantum Commerce

Servidor MCP em Python que expoe o catalogo extraido da planilha
`db_csv/quantum-commerce-chamados.xlsx`.

A planilha nao e um catalogo: cada linha e um **chamado**. A primeira Tool
deduplica por `id_produto` e agrupa por `categoria_produto`.

## Passo a passo da solucao

1. **Ler o Excel** — 200 chamados, aba `in`, colunas de produto
   (`id_produto`, `nome_produto`, `descricao_produto`, `categoria_produto`,
   `preco`, `avaliacao_produto`).
2. **Montar um catalogo** — 33 produtos unicos em 5 categorias:
   `brinquedo`, `game`, `livro`, `maquiagem`, `outros`.
3. **Criar o servidor MCP** — FastMCP registra Tools que um cliente de IA chama.
4. **Expor a Tool** `listar_produtos_por_categoria` — com filtro opcional.

## Estrutura

```
mcp_server/
  data_loader.py   # leitura do .xlsx e agrupamento
  server.py        # FastMCP + Tool
db_csv/
  quantum-commerce-chamados.xlsx
```

## Ambiente

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Como conectar no Cursor

Em **Cursor Settings → MCP**, adicione um servidor com comando apontando para
o Python do venv e o `server.py` (ajuste o caminho da sua maquina):

```json
{
  "mcpServers": {
    "quantum-commerce": {
      "command": "/CAMINHO/DO/PROJETO/.venv/bin/python",
      "args": ["/CAMINHO/DO/PROJETO/mcp_server/server.py"]
    }
  }
}
```

Depois peca ao agente: *liste os produtos da categoria game*.

## Chamada da Tool

| Parametro   | Efeito                                      |
|-------------|---------------------------------------------|
| `""`        | Todas as categorias                         |
| `"game"`    | So produtos da categoria game               |
| `"xyz"`     | Erro amigavel com as categorias validas     |
