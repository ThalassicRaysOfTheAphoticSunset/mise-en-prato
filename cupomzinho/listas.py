#!/usr/bin/env python3
"""Lista de compras, e a pergunta que ela existe para responder:

    "levando ESTA lista, qual mercado sai mais barato?"

A resposta nunca é só um número. Vem sempre com a **cobertura** — quantos itens
da lista aquela loja tem preço conhecido — porque loja que só cobre metade da
lista fatalmente parece a mais barata, e deixá-la ganhar em silêncio seria
mentir com estatística verdadeira.

Os preços saem de duas fontes na mesma série: o que a casa pagou (cupom) e o
que a região praticou (menor_preco, consultado sob demanda). Preço da própria
casa é fato consumado; da região é informação de terceiro, já validada pelo
sanidade.py antes de chegar aqui.
"""
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal

from .banco import milesimos, reais, unidades

# Janela larga DE PROPÓSITO. A API já devolve só o que ela considera vigente —
# e "vigente" para ela inclui preço de duas semanas atrás, medido em 19/08/2026:
# com data=7 vieram observações de 05/08. Filtrar de novo em 7 dias aqui era
# jogar fora dado que a fonte deu por bom, e fazia loja com preço válido sumir
# da comparação como se não vendesse o item. Quem julga se está velho é a tela,
# que mostra a data ao lado.
JANELA_PADRAO = 30
SEM_NOME = "loja não identificada"


# ── a lista ──────────────────────────────────────────────────────────────────
def criar(con: sqlite3.Connection, nome: str, morador: str) -> int:
    with con:
        return con.execute(
            "INSERT INTO lista (nome, morador, criada_em) VALUES (?,?,?)",
            (nome, morador, datetime.now().isoformat(sep=" ", timespec="seconds"))).lastrowid


def listar(con: sqlite3.Connection) -> list[dict]:
    return [
        {"id": r["id"], "nome": r["nome"], "morador": r["morador"],
         "criada_em": r["criada_em"], "itens": r["itens"]}
        for r in con.execute("""
            SELECT l.*, (SELECT COUNT(*) FROM lista_item li WHERE li.lista_id = l.id) AS itens
              FROM lista l ORDER BY l.criada_em DESC""")
    ]


def por_produto(con: sqlite3.Connection, lista_id: int, produto_id: int,
                quantidade: Decimal | None = None) -> None:
    """Põe (ou atualiza) um item na lista."""
    with con:
        con.execute(
            """INSERT INTO lista_item (lista_id, produto_id, quantidade_milesimos)
               VALUES (?,?,?)
               ON CONFLICT(lista_id, produto_id)
               DO UPDATE SET quantidade_milesimos = excluded.quantidade_milesimos""",
            (lista_id, produto_id, milesimos(quantidade or Decimal("1"))))


def tirar(con: sqlite3.Connection, lista_id: int, produto_id: int) -> None:
    with con:
        con.execute("DELETE FROM lista_item WHERE lista_id = ? AND produto_id = ?",
                    (lista_id, produto_id))


def itens(con: sqlite3.Connection, lista_id: int) -> list[dict]:
    return [
        {"produto_id": r["produto_id"],
         "descricao": r["apelido"] or r["descricao_canonica"],
         "gtin": r["gtin"], "quantidade": unidades(r["quantidade_milesimos"]),
         "is_granel": bool(r["is_granel"])}
        for r in con.execute("""
            SELECT li.produto_id, li.quantidade_milesimos, p.descricao_canonica,
                   p.apelido, p.gtin, p.is_granel
              FROM lista_item li JOIN produto p ON p.id = li.produto_id
             WHERE li.lista_id = ? ORDER BY p.descricao_canonica""", (lista_id,))
    ]


def sugerir(con: sqlite3.Connection, limite: int = 20) -> list[dict]:
    """O que a casa mais compra — o ponto de partida de qualquer lista.

    Ordena por número de compras e não por gasto: arroz comprado toda semana
    interessa mais numa lista do que um churrasco caro comprado uma vez.
    """
    return [
        {"produto_id": r["id"], "descricao": r["apelido"] or r["descricao_canonica"],
         "compras": r["compras"], "ultimo": reais(r["ultimo"]),
         "is_granel": bool(r["is_granel"])}
        for r in con.execute("""
            SELECT p.id, p.descricao_canonica, p.apelido, p.is_granel,
                   COUNT(DISTINCT i.chave) AS compras,
                   (SELECT i2.unitario_centavos FROM item i2
                      JOIN cupom c2 ON c2.chave = i2.chave
                     WHERE i2.produto_id = p.id ORDER BY c2.emitido_em DESC LIMIT 1) AS ultimo
              FROM produto p JOIN item i ON i.produto_id = p.id
             GROUP BY p.id ORDER BY compras DESC, p.descricao_canonica LIMIT ?""", (limite,))
    ]



# ── a comparação ─────────────────────────────────────────────────────────────
def _precos_recentes(con: sqlite3.Connection, produto_ids: list[int], dias: int) -> list[dict]:
    """Preço mais recente de cada produto em cada loja, dentro da janela."""
    if not produto_ids:
        return []
    limite = (datetime.now() - timedelta(days=dias)).isoformat(sep=" ", timespec="seconds")
    marcadores = ",".join("?" * len(produto_ids))
    return [dict(r) for r in con.execute(f"""
        SELECT produto_id, loja_id, loja, unitario_centavos, observado_em, fonte, distancia_km
          FROM (
            SELECT pr.produto_id, pr.distancia_km,
                   COALESCE(pr.cnpj, pr.codigo_menor_preco, pr.loja, '?') AS loja_id,
                   COALESCE(e.razao, pr.loja, ?) AS loja,
                   pr.unitario_centavos, pr.observado_em, pr.fonte,
                   ROW_NUMBER() OVER (
                       PARTITION BY pr.produto_id,
                                    COALESCE(pr.cnpj, pr.codigo_menor_preco, pr.loja, '?')
                       ORDER BY pr.observado_em DESC) AS ordem
              FROM preco pr LEFT JOIN estabelecimento e ON e.cnpj = pr.cnpj
             WHERE pr.produto_id IN ({marcadores})
               AND COALESCE(pr.observado_em, '') >= ?)
         WHERE ordem = 1""",  # noqa: S608 — marcadores são "?", não dado
        [SEM_NOME, *produto_ids, limite])]


def comparar(con: sqlite3.Connection, lista_id: int, dias: int = JANELA_PADRAO) -> dict:
    """Quanto a lista custa em cada loja, e o que falta em cada uma."""
    da_lista = itens(con, lista_id)
    if not da_lista:
        return {"lista_id": lista_id, "itens": 0, "lojas": [], "sem_preco": []}

    quantidade = {i["produto_id"]: i["quantidade"] for i in da_lista}
    nome = {i["produto_id"]: i["descricao"] for i in da_lista}
    precos = _precos_recentes(con, list(quantidade), dias)

    lojas: dict[str, dict] = {}
    for p in precos:
        loja = lojas.setdefault(p["loja_id"], {
            "loja": p["loja"], "total": Decimal("0"), "itens": [], "fontes": set(),
            "distancias": []})
        if p["distancia_km"] is not None:
            loja["distancias"].append(p["distancia_km"])
        valor = reais(p["unitario_centavos"]) * quantidade[p["produto_id"]]
        loja["total"] += valor
        loja["fontes"].add(p["fonte"])
        loja["itens"].append({
            "produto_id": p["produto_id"], "descricao": nome[p["produto_id"]],
            "unitario": reais(p["unitario_centavos"]), "subtotal": valor,
            "fonte": p["fonte"], "observado_em": p["observado_em"]})

    resultado = []
    for dados in lojas.values():
        tem = {i["produto_id"] for i in dados["itens"]}
        resultado.append({
            "loja": dados["loja"],
            # Rede grande tem várias filiais, e a API dá um código por filial.
            # Sem a distância, três linhas "COOPERATIVA AGROINDUSTRIAL LAR" com
            # totais diferentes são indistinguíveis na tela.
            "distancia_km": (round(min(dados["distancias"]), 1)
                             if dados["distancias"] else None),
            "total": dados["total"].quantize(Decimal("0.01")),
            # cobertura vai SEMPRE junto do total: sem ela, a loja que só tem
            # metade da lista parece a mais barata e ganha por omissão.
            "cobertura": len(tem), "de": len(quantidade),
            "completa": len(tem) == len(quantidade),
            "falta": sorted(nome[i] for i in set(quantidade) - tem),
            "fontes": sorted(dados["fontes"]),
            # a observação mais antiga do lote: é o que permite desconfiar de um
            # total barato demais sem precisar abrir loja por loja
            "mais_antigo": min((i["observado_em"] or "") for i in dados["itens"])[:10],
            "itens": sorted(dados["itens"], key=lambda i: i["descricao"]),
        })

    # completas primeiro, e entre elas a mais barata. Comparar total de listas
    # de tamanhos diferentes seria comparar coisas diferentes.
    resultado.sort(key=lambda r: (not r["completa"], r["total"]))
    com_preco = {p["produto_id"] for p in precos}
    return {
        "lista_id": lista_id, "itens": len(quantidade), "lojas": resultado,
        "sem_preco": sorted(nome[i] for i in set(quantidade) - com_preco),
        "janela_dias": dias,
    }
