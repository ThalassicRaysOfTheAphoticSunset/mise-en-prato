#!/usr/bin/env python3
"""Descobrir o código de barras de um produto, sem chutar.

O cupom fiscal não traz GTIN — traz a descrição que a loja imprime e um código
interno dela. O Menor Preço traz GTIN. Casar os dois transforma o catálogo da
casa de "textos parecidos" em "produtos identificados", e é isso que faz a
comparação entre mercados funcionar de verdade: com GTIN a busca é exata, e
duas grafias diferentes viram prova de que são o mesmo item em vez de palpite.

A regra é conservadora porque o erro é caro e silencioso: atribuir o GTIN
errado não quebra nada visivelmente, só passa a comparar o produto errado para
sempre. São dois degraus, e ambos exigem descrição IDÊNTICA depois de
normalizar — nunca aproximada.

  1. **mesma loja**: um resultado com a mesma descrição vindo de uma loja onde
     a casa comprou aquele item. É o degrau forte: os dois textos saíram do
     mesmo sistema de caixa, então bater é quase prova.
  2. **consenso da região**: sem loja em comum, aceita-se se TODOS os
     resultados com aquela descrição exata apontarem para um único GTIN.

Qualquer ambiguidade — dois GTIN diferentes para a mesma descrição — recusa e
deixa para a pessoa decidir. Ver unificacao.py, que é o outro lado disto.
"""
import logging
import re
import sqlite3

from .banco import reais
from .nfce_pr import normalizar
from .sanidade import digito_gtin_confere

log = logging.getLogger("cupomzinho.enriquecimento")


def _lojas_onde_a_casa_comprou(con: sqlite3.Connection, produto_id: int) -> set[str]:
    return {r["razao"].upper() for r in con.execute("""
        SELECT DISTINCT e.razao
          FROM item i JOIN cupom c ON c.chave = i.chave
          JOIN estabelecimento e ON e.cnpj = c.cnpj
         WHERE i.produto_id = ?""", (produto_id,)) if r["razao"]}


def _preco_pago(con: sqlite3.Connection, produto_id: int) -> set[str]:
    """Preços unitários já pagos, em texto igual ao que a API devolve."""
    return {f"{reais(r['unitario_centavos']):.2f}" for r in con.execute(
        "SELECT DISTINCT unitario_centavos FROM item WHERE produto_id = ?", (produto_id,))}


def escolher_gtin(descricao: str, achados: list[dict], *,
                  lojas_da_casa: set[str], precos_pagos: set[str]) -> dict:
    """Decide se dá para atribuir um GTIN. Devolve o motivo em qualquer caso."""
    alvo = normalizar(descricao)
    iguais = [p for p in achados
              if normalizar(p.get("desc", "")) == alvo
              and digito_gtin_confere(p.get("gtin") or "")]
    if not iguais:
        return {"gtin": None, "motivo": "nenhum resultado com a descrição exata"}

    def codigo(p):
        return re.sub(r"\D", "", p["gtin"]).lstrip("0")

    # degrau 1: mesma loja onde a casa comprou
    da_casa = [p for p in iguais
               if (p.get("estabelecimento") or {}).get("nm_emp", "").upper() in lojas_da_casa]
    if da_casa:
        codigos = {codigo(p) for p in da_casa}
        if len(codigos) == 1:
            gtin = codigos.pop()
            mesmo_preco = any(p.get("valor") in precos_pagos for p in da_casa)
            return {"gtin": gtin, "ncm": da_casa[0].get("ncm"),
                    "motivo": "mesma descrição na mesma loja onde a casa comprou"
                              + (", e pelo mesmo preço" if mesmo_preco else ""),
                    "confianca": "alta"}
        return {"gtin": None,
                "motivo": f"a mesma loja lista {len(codigos)} códigos para essa descrição"}

    # degrau 2: consenso — a região inteira concorda num código só
    codigos = {codigo(p) for p in iguais}
    if len(codigos) == 1:
        return {"gtin": codigos.pop(), "ncm": iguais[0].get("ncm"),
                "motivo": f"{len(iguais)} resultados na região, todos com o mesmo código",
                "confianca": "media"}
    return {"gtin": None,
            "motivo": f"{len(codigos)} códigos diferentes para a mesma descrição — ambíguo"}


def aplicar(con: sqlite3.Connection, produto_id: int, gtin: str, ncm: str | None) -> None:
    with con:
        con.execute(
            "UPDATE produto SET gtin = ?, ncm = COALESCE(ncm, ?) WHERE id = ?",
            (gtin, ncm, produto_id))


def enriquecer(con: sqlite3.Connection, cliente, produto_id: int) -> dict:
    """Uma consulta ao Menor Preço para descobrir o GTIN de um produto."""
    produto = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if not produto:
        raise ValueError("produto não encontrado")
    if produto["gtin"]:
        return {"produto_id": produto_id, "gtin": produto["gtin"],
                "motivo": "já tinha código de barras", "novo": False}

    descricao = produto["descricao_canonica"]
    achados = cliente.buscar(termo=descricao)
    decisao = escolher_gtin(
        descricao, achados,
        lojas_da_casa=_lojas_onde_a_casa_comprou(con, produto_id),
        precos_pagos=_preco_pago(con, produto_id))

    # Marca a tentativa dando certo ou não: é isso que impede a cebola de
    # voltar ao topo da fila toda rodada e queimar o teto de consultas.
    with con:
        con.execute("UPDATE produto SET gtin_buscado_em = datetime('now','localtime')"
                    " WHERE id = ?", (produto_id,))
    if decisao["gtin"]:
        aplicar(con, produto_id, decisao["gtin"], decisao.get("ncm"))
        log.info("produto %s (%s) -> gtin %s (%s)",
                 produto_id, descricao, decisao["gtin"], decisao["motivo"])
    return {"produto_id": produto_id, "descricao": descricao, "novo": bool(decisao["gtin"]),
            **decisao}


def pendentes(con: sqlite3.Connection, limite: int = 50) -> list[dict]:
    """Produtos sem código de barras. Quem nunca foi perguntado vem primeiro.

    A ordem importa porque cada produto custa uma consulta do teto por hora:
    perguntar de novo sobre a cebola a granel, que nunca terá código, é gastar
    a cota com uma resposta já conhecida.
    """
    return [
        {"produto_id": r["id"], "descricao": r["descricao_canonica"], "compras": r["compras"],
         "ja_perguntado": bool(r["gtin_buscado_em"])}
        for r in con.execute("""
            SELECT p.id, p.descricao_canonica, p.gtin_buscado_em,
                   COUNT(DISTINCT i.chave) AS compras
              FROM produto p JOIN item i ON i.produto_id = p.id
             WHERE p.gtin IS NULL
             GROUP BY p.id
             ORDER BY (p.gtin_buscado_em IS NOT NULL), p.gtin_buscado_em,
                      compras DESC, p.descricao_canonica
             LIMIT ?""", (limite,))
    ]
