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
from .catalogo import (
    buscar_por_gtin,
    buscar_por_termo,
    conectar_catalogo,
    consultar_openfoodfacts_api,
    sincronizar_gtins_existentes,
)
from .nfce_pr import normalizar
from .sanidade import digito_gtin_confere

log = logging.getLogger("cupomzinho.enriquecimento")


def buscar_metadados_ean(gtin: str, timeout: int = 5) -> dict | None:
    """Busca metadados completos de um GTIN/EAN via catálogo local ou Open Food Facts."""
    cat = conectar_catalogo(somente_leitura=True)
    if cat:
        info = buscar_por_gtin(cat, gtin, consultar_online_se_faltar=True)
        cat.close()
        if info:
            return info
    return consultar_openfoodfacts_api(gtin, timeout=timeout)


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


def aplicar(
    con: sqlite3.Connection,
    produto_id: int,
    gtin: str,
    ncm: str | None = None,
    *,
    marca: str | None = None,
    nutriscore: str | None = None,
    nova_group: int | None = None,
    foto_url: str | None = None,
) -> None:
    with con:
        con.execute(
            """UPDATE produto
                  SET gtin = COALESCE(?, gtin),
                      ncm = COALESCE(ncm, ?),
                      marca = COALESCE(marca, ?),
                      nutriscore = COALESCE(nutriscore, ?),
                      nova_group = COALESCE(nova_group, ?),
                      foto_url = COALESCE(foto_url, ?)
                WHERE id = ?""",
            (gtin, ncm, marca, nutriscore, nova_group, foto_url, produto_id),
        )


def enriquecer_local(
    con: sqlite3.Connection,
    produto_id: int,
    con_catalogo: sqlite3.Connection | None = None,
) -> dict:
    """Enriquece produto utilizando o banco satélite local (catalogo.db).

    Zero chamadas de rede, sem risco de rate limit.
    """
    produto = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if not produto:
        raise ValueError("produto não encontrado")

    if produto["is_granel"]:
        return {
            "produto_id": produto_id,
            "descricao": produto["descricao_canonica"],
            "gtin": None,
            "motivo": "produto a granel não possui código de barras comercial",
            "novo": False,
        }

    cat = con_catalogo if con_catalogo is not None else conectar_catalogo(somente_leitura=True)
    if not cat:
        return {
            "produto_id": produto_id,
            "descricao": produto["descricao_canonica"],
            "gtin": produto["gtin"],
            "motivo": "catálogo offline não inicializado",
            "novo": False,
        }

    descricao = produto["descricao_canonica"]
    gtin_atual = produto["gtin"]

    # Caso 1: O produto já possui GTIN (ex: veio do SPED cEAN ou de enriquecimento anterior)
    if gtin_atual:
        info = buscar_por_gtin(cat, gtin_atual, consultar_online_se_faltar=True)
        if info:
            aplicar(
                con,
                produto_id,
                gtin_atual,
                info.get("ncm"),
                marca=info.get("marca"),
                nutriscore=info.get("nutriscore"),
                nova_group=info.get("nova_group"),
                foto_url=info.get("foto_url"),
            )
            return {
                "produto_id": produto_id,
                "descricao": descricao,
                "gtin": gtin_atual,
                "novo": True,
                "motivo": f"metadados enriquecidos via catálogo local ({info.get('origem')})",
                "catalogo": info,
            }
        return {
            "produto_id": produto_id,
            "descricao": descricao,
            "gtin": gtin_atual,
            "novo": False,
            "motivo": "já tinha GTIN mas não encontrado no catálogo local",
        }

    # Caso 2: O produto não possui GTIN - busca no catálogo por correspondência de descrição
    termos = buscar_por_termo(cat, descricao, limite=5)
    alvo = normalizar(descricao)
    exatos = [t for t in termos if normalizar(t["nome"]) == alvo and digito_gtin_confere(t["gtin"])]

    if len(exatos) == 1:
        item_cat = exatos[0]
        aplicar(
            con,
            produto_id,
            item_cat["gtin"],
            item_cat.get("ncm"),
            marca=item_cat.get("marca"),
            nutriscore=item_cat.get("nutriscore"),
            nova_group=item_cat.get("nova_group"),
            foto_url=item_cat.get("foto_url"),
        )
        log.info(
            "produto %s (%s) -> gtin %s via catálogo local (%s)",
            produto_id,
            descricao,
            item_cat["gtin"],
            item_cat.get("origem"),
        )
        return {
            "produto_id": produto_id,
            "descricao": descricao,
            "gtin": item_cat["gtin"],
            "novo": True,
            "motivo": f"resolvido via catálogo local ({item_cat.get('origem')})",
            "catalogo": item_cat,
        }

    return {
        "produto_id": produto_id,
        "descricao": descricao,
        "gtin": None,
        "novo": False,
        "motivo": "não encontrado no catálogo local"
        if not exatos
        else f"{len(exatos)} itens ambíguos no catálogo local",
    }


def enriquecer(
    con: sqlite3.Connection,
    cliente,
    produto_id: int,
    con_catalogo: sqlite3.Connection | None = None,
) -> dict:
    """Enriquecimento em cascata: catálogo local primeiro, Menor Preço depois."""
    produto = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    if not produto:
        raise ValueError("produto não encontrado")

    if produto["is_granel"]:
        return {
            "produto_id": produto_id,
            "descricao": produto["descricao_canonica"],
            "gtin": None,
            "motivo": "produto a granel não possui código de barras comercial",
            "novo": False,
        }

    if produto["gtin"]:
        enriquecer_local(con, produto_id, con_catalogo)
        return {
            "produto_id": produto_id,
            "gtin": produto["gtin"],
            "motivo": "já tinha código de barras",
            "novo": False,
        }

    # Degrau 1: Catálogo Satélite Local (Offline, sem requisição de rede)
    res_local = enriquecer_local(con, produto_id, con_catalogo)
    if res_local["novo"]:
        return res_local

    # Degrau 2: Menor Preço CELEPAR (sob demanda com teto horário)
    descricao = produto["descricao_canonica"]
    achados = cliente.buscar(termo=descricao)
    decisao = escolher_gtin(
        descricao,
        achados,
        lojas_da_casa=_lojas_onde_a_casa_comprou(con, produto_id),
        precos_pagos=_preco_pago(con, produto_id),
    )

    # Marca a tentativa dando certo ou não: é isso que impede quem não teve
    # consenso de voltar ao topo da fila toda rodada e queimar o teto de consultas.
    with con:
        con.execute(
            "UPDATE produto SET gtin_buscado_em = datetime('now','localtime')"
            " WHERE id = ?",
            (produto_id,),
        )
    if decisao["gtin"]:
        aplicar(con, produto_id, decisao["gtin"], decisao.get("ncm"))
        # Se achou GTIN pelo Menor Preço, tenta enriquecer dados nutricionais do catálogo local
        enriquecer_local(con, produto_id, con_catalogo)
        log.info(
            "produto %s (%s) -> gtin %s (%s)",
            produto_id,
            descricao,
            decisao["gtin"],
            decisao["motivo"],
        )
    return {
        "produto_id": produto_id,
        "descricao": descricao,
        "novo": bool(decisao["gtin"]),
        **decisao,
    }


def enriquecer_pendentes_local(
    con: sqlite3.Connection,
    con_catalogo: sqlite3.Connection | None = None,
    limite: int = 100,
) -> dict:
    """Percorre produtos com GTIN sem metadados e produtos sem GTIN, enriquecendo via catálogo."""
    cat = con_catalogo if con_catalogo is not None else conectar_catalogo(criar=True)
    relatorio = {"identificados": [], "sem_certeza": [], "metadados_atualizados": 0}

    # 1. Atualiza produtos que já têm GTIN mas faltam marca/nutriscore/nova
    if cat:
        sync = sincronizar_gtins_existentes(con, cat, limite=limite)
        relatorio["metadados_atualizados"] = sync.get("atualizados", 0)

    # 2. Tenta resolver itens pendentes sem GTIN
    for pendente in pendentes(con, limite):
        r = enriquecer_local(con, pendente["produto_id"], cat)
        alvo = "identificados" if r["novo"] else "sem_certeza"
        relatorio[alvo].append(
            {"descricao": r["descricao"], "gtin": r.get("gtin"), "motivo": r["motivo"]}
        )
    relatorio["restantes"] = len(pendentes(con, 999))
    return relatorio


def pendentes(con: sqlite3.Connection, limite: int = 50) -> list[dict]:
    """Produtos sem código de barras. Quem nunca foi perguntado vem primeiro.

    Produtos a granel (is_granel = 1) nunca são incluídos: cebola e carnes a peso
    nunca terão GTIN comercial, e perguntar à CELEPAR sobre eles seria queimar a cota.
    Produtos com variantes cadastradas em produto_variante também não são pendentes.
    """
    return [
        {
            "produto_id": r["id"],
            "descricao": r["descricao_canonica"],
            "compras": r["compras"],
            "ja_perguntado": bool(r["gtin_buscado_em"]),
        }
        for r in con.execute(
            """
            SELECT p.id, p.descricao_canonica, p.gtin_buscado_em,
                   COUNT(DISTINCT i.chave) AS compras
              FROM produto p JOIN item i ON i.produto_id = p.id
             WHERE p.gtin IS NULL AND p.is_granel = 0
               AND NOT EXISTS (SELECT 1 FROM produto_variante pv WHERE pv.produto_id = p.id)
             GROUP BY p.id
             ORDER BY (p.gtin_buscado_em IS NOT NULL), p.gtin_buscado_em,
                      compras DESC, p.descricao_canonica
             LIMIT ?""",
            (limite,),
        )
    ]

