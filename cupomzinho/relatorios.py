#!/usr/bin/env python3
"""As perguntas que o banco responde: quanto, onde, com quem e desde quando.

Tudo em SQL explícito, e todo dinheiro sai daqui já em Decimal. Somar centavos
inteiros no sqlite é exato; somar Decimal em python depois de um float não é.
"""
import sqlite3
from datetime import date
from decimal import Decimal

from .banco import reais, unidades

SEM_CATEGORIA = "sem categoria"


def _quanto(linha) -> dict:
    """Quanto se comprou, em unidade de verdade — não em linhas de cupom.

    Contar linha é o erro fácil e mentiroso nos dois sentidos: seis garrafas
    numa linha só viravam "1x", e duas latas do mesmo atum na mesma nota
    viravam "2x" como se fossem duas idas ao mercado.

    Quando o produto aparece com unidades diferentes entre compras (raro, mas
    acontece quando a loja troca de cadastro), somar seria inventar: a
    quantidade sai como None e a tela mostra só o número de compras.
    """
    if linha["unidades"] != 1:
        return {"quantidade": None, "unidade": None}
    return {"quantidade": unidades(linha["quantidade"]), "unidade": linha["unidade"]}


def meses_com_gasto(con: sqlite3.Connection, limite: int = 12) -> list[dict]:
    """Série mensal, do mês mais recente para trás. É o gráfico da aba Gastos."""
    return [
        {"mes": r["mes"], "total": reais(r["total"]), "cupons": r["cupons"]}
        for r in con.execute(
            """SELECT strftime('%Y-%m', emitido_em) AS mes,
                      SUM(pago_centavos) AS total, COUNT(*) AS cupons
                 FROM cupom
                WHERE emitido_em IS NOT NULL
                GROUP BY mes ORDER BY mes DESC LIMIT ?""", (limite,))
    ]


def _fatia(con, sql: str, mes: str, rotulo: str) -> list[dict]:
    return [
        {rotulo: r["rotulo"] or SEM_CATEGORIA, "total": reais(r["total"]), "cupons": r["cupons"]}
        for r in con.execute(sql, (mes,))
    ]


def resumo_do_mes(con: sqlite3.Connection, mes: str | None = None) -> dict:
    """Fecha o mês: total, quebra por mercado e por morador, e o mês anterior.

    `mes` no formato "2026-08". Sem argumento, o mês corrente.
    """
    mes = mes or date.today().strftime("%Y-%m")
    ano, m = (int(p) for p in mes.split("-"))
    anterior = f"{ano - 1:04d}-12" if m == 1 else f"{ano:04d}-{m - 1:02d}"

    def total_de(alvo: str) -> Decimal:
        bruto = con.execute(
            "SELECT SUM(pago_centavos) FROM cupom WHERE strftime('%Y-%m', emitido_em) = ?",
            (alvo,)).fetchone()[0]
        return reais(bruto or 0)

    total = total_de(mes)
    total_anterior = total_de(anterior)

    por_loja = _fatia(con, """
        SELECT e.razao AS rotulo, SUM(c.pago_centavos) AS total, COUNT(*) AS cupons
          FROM cupom c JOIN estabelecimento e ON e.cnpj = c.cnpj
         WHERE strftime('%Y-%m', c.emitido_em) = ?
         GROUP BY e.cnpj ORDER BY total DESC""", mes, "loja")

    por_morador = _fatia(con, """
        SELECT morador AS rotulo, SUM(pago_centavos) AS total, COUNT(*) AS cupons
          FROM cupom WHERE strftime('%Y-%m', emitido_em) = ?
         GROUP BY morador ORDER BY total DESC""", mes, "morador")

    # A categoria vem do NCM, e o NCM só chega pelo Menor Preço. Enquanto o
    # enriquecimento não roda, quase tudo cai em "sem categoria" — e é assim
    # que tem de aparecer na tela, em vez de fingir uma classificação.
    por_categoria = [
        {"categoria": r["rotulo"] or SEM_CATEGORIA, "total": reais(r["total"]),
         "itens": r["itens"]}
        for r in con.execute("""
            SELECT p.categoria AS rotulo, SUM(i.total_centavos) AS total, COUNT(*) AS itens
              FROM item i
              JOIN cupom c ON c.chave = i.chave
              LEFT JOIN produto p ON p.id = i.produto_id
             WHERE strftime('%Y-%m', c.emitido_em) = ?
             GROUP BY p.categoria ORDER BY total DESC""", (mes,))
    ]

    maiores = [
        {"produto_id": r["produto_id"], "descricao": r["descricao"],
         "total": reais(r["total"]), "compras": r["compras"],
         **_quanto(r)}
        for r in con.execute("""
            SELECT i.produto_id, p.descricao_canonica AS descricao,
                   SUM(i.total_centavos) AS total,
                   -- compras = idas ao mercado, NÃO linhas de cupom: o mesmo
                   -- produto pode aparecer em duas linhas da mesma nota.
                   COUNT(DISTINCT i.chave) AS compras,
                   SUM(i.quantidade_milesimos) AS quantidade,
                   COUNT(DISTINCT i.unidade) AS unidades,
                   MAX(i.unidade) AS unidade
              FROM item i
              JOIN cupom c ON c.chave = i.chave
              JOIN produto p ON p.id = i.produto_id
             WHERE strftime('%Y-%m', c.emitido_em) = ?
             GROUP BY i.produto_id ORDER BY total DESC LIMIT 15""", (mes,))
    ]

    # Perfil nutricional (Classificação NOVA de ultraprocessados)
    rotulos_nova = {
        1: "In natura / minimamente processados",
        2: "Ingredientes culinários",
        3: "Alimentos processados",
        4: "Alimentos ultraprocessados",
    }
    por_nova = [
        {
            "grupo": r["nova_group"],
            "rotulo": rotulos_nova.get(r["nova_group"], f"Grupo {r['nova_group']}"),
            "total": reais(r["total"]),
            "itens": r["itens"],
        }
        for r in con.execute("""
            SELECT p.nova_group, SUM(i.total_centavos) AS total, COUNT(*) AS itens
              FROM item i
              JOIN cupom c ON c.chave = i.chave
              JOIN produto p ON p.id = i.produto_id
             WHERE strftime('%Y-%m', c.emitido_em) = ? AND p.nova_group IS NOT NULL
             GROUP BY p.nova_group ORDER BY p.nova_group""", (mes,))
    ]
    total_classificado = sum((n["total"] for n in por_nova), Decimal("0.00"))
    gasto_ultra = next((n["total"] for n in por_nova if n["grupo"] == 4), Decimal("0.00"))
    pct_ultra = (
        round(float(gasto_ultra / total_classificado * 100), 1)
        if total_classificado > 0
        else 0.0
    )

    return {
        "mes": mes, "total": total,
        "mes_anterior": anterior, "total_anterior": total_anterior,
        "variacao": total - total_anterior,
        "cupons": con.execute(
            "SELECT COUNT(*) FROM cupom WHERE strftime('%Y-%m', emitido_em) = ?",
            (mes,)).fetchone()[0],
        "por_loja": por_loja, "por_morador": por_morador,
        "por_categoria": por_categoria, "maiores": maiores,
        "perfil_nutricional": {
            "por_nova": por_nova,
            "total_classificado": total_classificado,
            "percentual_ultraprocessados": pct_ultra,
        },
    }


def produtos(con: sqlite3.Connection, busca: str = "", limite: int = 100) -> list[dict]:
    """Catálogo do que a casa já comprou, do mais gasto para o menos."""
    like = f"%{busca.upper()}%"
    return [
        {"id": r["id"], "descricao": r["apelido"] or r["descricao_canonica"],
         "gtin": r["gtin"], "categoria": r["categoria"],
         "marca": r["marca"], "nutriscore": r["nutriscore"],
         "nova_group": r["nova_group"], "foto_url": r["foto_url"],
         "acompanhar": bool(r["acompanhar"]), "compras": r["compras"],
         "gasto": reais(r["gasto"] or 0), "ultimo": reais(r["ultimo"]),
         **_quanto(r)}
        for r in con.execute("""
            SELECT p.*, COUNT(DISTINCT i.chave) AS compras,
                   SUM(i.quantidade_milesimos) AS quantidade,
                   COUNT(DISTINCT i.unidade) AS unidades,
                   MAX(i.unidade) AS unidade,
                   SUM(i.total_centavos) AS gasto,
                   (SELECT i2.unitario_centavos FROM item i2
                      JOIN cupom c2 ON c2.chave = i2.chave
                     WHERE i2.produto_id = p.id
                     ORDER BY c2.emitido_em DESC LIMIT 1) AS ultimo
              FROM produto p LEFT JOIN item i ON i.produto_id = p.id
             WHERE (? = '' OR UPPER(p.descricao_canonica) LIKE ?)
             GROUP BY p.id ORDER BY gasto DESC NULLS LAST LIMIT ?""",
            (busca, like, limite))
    ]


def historico_do_produto(con: sqlite3.Connection, produto_id: int) -> dict:
    """O que a casa pagou, quando e onde — e o que a região praticou.

    As duas fontes saem separadas de propósito: 'cupom' é fato consumado da
    casa; 'menor_preco' é informação de terceiro, e só entra depois de passar
    pelo detector de dado envenenado.
    """
    produto = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if not produto:
        return {}

    serie = [
        {"data": r["observado_em"], "valor": reais(r["unitario_centavos"]),
         "fonte": r["fonte"], "loja": r["razao"]}
        for r in con.execute("""
            SELECT pr.observado_em, pr.unitario_centavos, pr.fonte, e.razao
              FROM preco pr LEFT JOIN estabelecimento e ON e.cnpj = pr.cnpj
             WHERE pr.produto_id = ? ORDER BY pr.observado_em""", (produto_id,))
    ]
    pagos = sorted(p["valor"] for p in serie if p["fonte"] == "cupom")
    compras = [
        {"data": r["emitido_em"], "loja": r["razao"],
         "quantidade": unidades(r["quantidade_milesimos"]), "unidade": r["unidade"],
         "valor_unitario": reais(r["unitario_centavos"]),
         "valor_total": reais(r["total_centavos"])}
        for r in con.execute("""
            SELECT i.*, c.emitido_em, e.razao
              FROM item i JOIN cupom c ON c.chave = i.chave
              JOIN estabelecimento e ON e.cnpj = c.cnpj
             WHERE i.produto_id = ? ORDER BY c.emitido_em DESC""", (produto_id,))
    ]
    return {
        "id": produto["id"],
        "descricao": produto["apelido"] or produto["descricao_canonica"],
        "descricao_canonica": produto["descricao_canonica"],
        "gtin": produto["gtin"], "categoria": produto["categoria"],
        "marca": produto["marca"],
        "nutriscore": produto["nutriscore"],
        "nova_group": produto["nova_group"],
        "foto_url": produto["foto_url"],
        "acompanhar": bool(produto["acompanhar"]),
        "total_compras": len({c["data"] for c in compras}),
        # Só soma se a unidade for a mesma em todas as compras — somar 2 CX com
        # 0,5 KG daria um número que não significa nada.
        "quantidade_total": (sum((c["quantidade"] for c in compras), Decimal("0"))
                             if len({c["unidade"] for c in compras}) == 1 else None),
        "unidade": compras[0]["unidade"] if len({c["unidade"] for c in compras}) == 1 else None,
        "mediana_paga": pagos[len(pagos) // 2] if pagos else None,
        "menor_pago": pagos[0] if pagos else None,
        "maior_pago": pagos[-1] if pagos else None,
        "serie": serie, "compras": compras,
        "variantes": _variantes_do_produto(con, produto_id),
    }


def _variantes_do_produto(con: sqlite3.Connection, produto_id: int) -> list[dict]:
    try:
        from .catalogo import listar_variantes
        return listar_variantes(con, produto_id)
    except Exception:
        return []
