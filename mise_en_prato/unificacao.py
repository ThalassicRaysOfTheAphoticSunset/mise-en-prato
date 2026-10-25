#!/usr/bin/env python3
"""Juntar o mesmo produto escrito de jeitos diferentes.

O cupom não traz código de barras, e cada loja imprime o nome como quer. Pior:
a MESMA loja imprime de dois jeitos na MESMA nota — o Muffato mandou
"ENERG MONST 473ML" e "ENERG MONSTER 473ML" no mesmo cupom.

Aqui há duas metades, e a separação é proposital:

  `candidatos()` SUGERE, e é conservador. Prefere deixar passar um par que era
  o mesmo produto a juntar dois que não eram — desfazer unificação é trabalhoso
  e o histórico fica sujo no meio-tempo.

  `unificar()` EXECUTA, e só a pedido de gente. Nunca automático, nem para o
  candidato mais óbvio: quem decide que dois nomes são a mesma coisa é quem
  compra.
"""
import logging
import re
import sqlite3

log = logging.getLogger("mise_en_prato.unificacao")

# Sufixos de tamanho: 170G, 473ML, 1LT, 2L. Servem de âncora — dois nomes só
# são candidatos se o tamanho bater, porque leite de 1 L e de 2 L são coisas
# diferentes por mais parecido que o nome seja.
TAMANHO = re.compile(r"^\d+([.,]\d+)?(G|GR|KG|ML|L|LT|UN|CM|M)$")


def _fichas(descricao: str) -> list[str]:
    return descricao.split()


def _mesmo_tamanho(a: list[str], b: list[str]) -> bool:
    ta = [f for f in a if TAMANHO.match(f)]
    tb = [f for f in b if TAMANHO.match(f)]
    return ta == tb


def parecidos(a: str, b: str) -> str | None:
    """Devolve o motivo se `a` e `b` parecem o mesmo produto, senão None.

    Regra única e estreita: mesmo número de fichas, tamanhos iguais, e no
    máximo UMA ficha diferente — sendo que a diferente tem de ser abreviação da
    outra (uma é prefixo da outra, com pelo menos 3 letras).

    É o bastante para MONST/MONSTER e INT/INTEGRAL, e recusa de propósito os
    pares que enganam: RALADO/PEDACOS e BAUNILHA/MGO têm o mesmo formato e são
    produtos diferentes. Abreviação é prefixo; sabor não é.
    """
    fa, fb = _fichas(a), _fichas(b)
    if len(fa) != len(fb) or not _mesmo_tamanho(fa, fb):
        return None

    diferentes = [(x, y) for x, y in zip(fa, fb, strict=True) if x != y]
    if not diferentes:
        return "descrições idênticas depois de normalizar"
    if len(diferentes) > 1:
        return None

    curta, longa = sorted(diferentes[0], key=len)
    if len(curta) >= 3 and longa.startswith(curta):
        return f"{curta} parece abreviação de {longa}"
    return None


def _info_produto_comparativo(
    con: sqlite3.Connection, produto_id: int, desc_fallback: str, linhas: int, gasto: int | None
) -> dict:
    prod = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()

    dados_compras = con.execute(
        """
        SELECT MIN(i.unitario_centavos) AS menor_preco,
               MAX(i.unitario_centavos) AS maior_preco,
               MAX(c.emitido_em) AS ultima_data
          FROM item i
          JOIN cupom c ON c.chave = i.chave
         WHERE i.produto_id = ?
    """,
        (produto_id,),
    ).fetchone()

    ultima_loja = None
    if dados_compras and dados_compras["ultima_data"]:
        loja_row = con.execute(
            """
            SELECT COALESCE(e.fantasia, e.razao) AS loja
              FROM cupom c
              LEFT JOIN estabelecimento e ON e.cnpj = c.cnpj
              JOIN item i ON i.chave = c.chave
             WHERE i.produto_id = ? AND c.emitido_em = ?
             LIMIT 1
        """,
            (produto_id, dados_compras["ultima_data"]),
        ).fetchone()
        if loja_row and loja_row["loja"]:
            ultima_loja = loja_row["loja"]

    sabores_set = set()
    try:
        sql_var = (
            "SELECT DISTINCT sabor FROM produto_variante "
            "WHERE produto_id = ? AND sabor IS NOT NULL"
        )
        for r in con.execute(sql_var, (produto_id,)):
            if r["sabor"]:
                sabores_set.add(r["sabor"])
        sql_item = (
            "SELECT DISTINCT sabor_adicional FROM item "
            "WHERE produto_id = ? AND sabor_adicional IS NOT NULL"
        )
        for r in con.execute(sql_item, (produto_id,)):
            if r["sabor_adicional"]:
                sabores_set.add(r["sabor_adicional"])
    except sqlite3.OperationalError:
        pass

    return {
        "id": produto_id,
        "descricao": prod["descricao_canonica"] if prod else desc_fallback,
        "gtin": prod["gtin"] if prod else None,
        "foto_url": prod["foto_url"] if prod else None,
        "marca": prod["marca"] if prod else None,
        "linhas": linhas,
        "gasto": gasto or 0,
        "menor_preco": dados_compras["menor_preco"] if dados_compras else None,
        "maior_preco": dados_compras["maior_preco"] if dados_compras else None,
        "ultima_data": dados_compras["ultima_data"] if dados_compras else None,
        "ultima_loja": ultima_loja,
        "sabores": sorted(list(sabores_set)),
    }


def candidatos(con: sqlite3.Connection) -> list[dict]:
    """Pares que valem uma olhada humana, do mais forte para o mais fraco."""
    produtos = list(con.execute("""
        SELECT p.id, p.descricao_canonica AS desc, p.gtin,
               COUNT(i.id) AS linhas, SUM(i.total_centavos) AS gasto
          FROM produto p LEFT JOIN item i ON i.produto_id = p.id
         GROUP BY p.id"""))
    recusados = {(r["desc_a"], r["desc_b"])
                 for r in con.execute("SELECT desc_a, desc_b FROM unificacao_recusada")}

    variantes_por_produto: dict[int, set[str]] = {}
    try:
        for r in con.execute("SELECT produto_id, gtin FROM produto_variante"):
            variantes_por_produto.setdefault(r["produto_id"], set()).add(r["gtin"])
    except sqlite3.OperationalError:
        pass

    infos_por_id: dict[int, dict] = {}

    def get_info(prod_row):
        pid = prod_row["id"]
        if pid not in infos_por_id:
            infos_por_id[pid] = _info_produto_comparativo(
                con, pid, prod_row["desc"], prod_row["linhas"], prod_row["gasto"]
            )
        return infos_por_id[pid]

    achados = []
    for k, a in enumerate(produtos):
        for b in produtos[k + 1:]:
            if tuple(sorted((a["desc"], b["desc"]))) in recusados:
                continue

            gtins_a = set(variantes_por_produto.get(a["id"], set()))
            if a["gtin"]:
                gtins_a.add(a["gtin"])
            gtins_b = set(variantes_por_produto.get(b["id"], set()))
            if b["gtin"]:
                gtins_b.add(b["gtin"])

            compartilhados = gtins_a.intersection(gtins_b)
            # Código de barras igual é prova, não indício — vem sempre primeiro.
            if a["gtin"] and a["gtin"] == b["gtin"]:
                motivo, forca = "mesmo código de barras", 0
            elif compartilhados:
                motivo, forca = "compartilham código de barras de sabor/variante", 0
            else:
                motivo = parecidos(a["desc"], b["desc"])
                if not motivo:
                    continue
                forca = 1
            achados.append({
                "forca": forca, "motivo": motivo,
                # o que tem mais histórico fica, para mexer em menos linhas
                "manter": get_info(a),
                "juntar": get_info(b),
            })
    for c in achados:
        if c["juntar"]["linhas"] > c["manter"]["linhas"]:
            c["manter"], c["juntar"] = c["juntar"], c["manter"]
    return sorted(achados, key=lambda c: (c["forca"], -c["manter"]["linhas"]))


def unificar(con: sqlite3.Connection, manter: int, juntar: int) -> dict:
    """Move todo o histórico de `juntar` para `manter` e apaga `juntar`."""
    if manter == juntar:
        raise ValueError("não dá para unificar um produto com ele mesmo")
    alvo = con.execute("SELECT * FROM produto WHERE id = ?", (manter,)).fetchone()
    outro = con.execute("SELECT * FROM produto WHERE id = ?", (juntar,)).fetchone()
    if not alvo or not outro:
        raise ValueError("produto não encontrado")

    with con:
        # Move variantes e memórias de loja de juntar para manter antes do DELETE
        try:
            con.execute(
                "UPDATE OR IGNORE produto_variante SET produto_id = ? WHERE produto_id = ?",
                (manter, juntar),
            )
            con.execute("DELETE FROM produto_variante WHERE produto_id = ?", (juntar,))
            con.execute(
                "UPDATE OR IGNORE codigo_loja_produto SET produto_id = ? WHERE produto_id = ?",
                (manter, juntar),
            )
            con.execute("DELETE FROM codigo_loja_produto WHERE produto_id = ?", (juntar,))
        except sqlite3.OperationalError:
            pass

        # OR IGNORE nos que têm índice único: a mesma observação de preço pode
        # já existir dos dois lados, e aí a cópia sobrando é descartada.
        con.execute("UPDATE item SET produto_id = ? WHERE produto_id = ?", (manter, juntar))
        con.execute("UPDATE OR IGNORE preco SET produto_id = ? WHERE produto_id = ?",
                    (manter, juntar))
        con.execute("DELETE FROM preco WHERE produto_id = ?", (juntar,))
        con.execute("UPDATE apelido_loja SET produto_id = ? WHERE produto_id = ?",
                    (manter, juntar))
        con.execute("UPDATE OR IGNORE lista_item SET produto_id = ? WHERE produto_id = ?",
                    (manter, juntar))
        con.execute("DELETE FROM lista_item WHERE produto_id = ?", (juntar,))

        # Apaga PRIMEIRO e herda depois: enquanto os dois existem, copiar o
        # código de barras de um para o outro deixaria dois produtos com o
        # mesmo gtin no mesmo instante.
        con.execute("DELETE FROM produto WHERE id = ?", (juntar,))
        campos_heranca = (
            "gtin", "ncm", "categoria", "apelido", "marca", "nutriscore", "nova_group", "foto_url"
        )
        for campo in campos_heranca:
            if campo in alvo.keys() and not alvo[campo] and outro[campo]:
                con.execute(f"UPDATE produto SET {campo} = ? WHERE id = ?",  # noqa: S608
                            (outro[campo], manter))

        # Se manter não tinha GTIN mas agora tem variantes, define o padrão
        try:
            var_padrao = con.execute(
                """
                SELECT gtin FROM produto_variante
                 WHERE produto_id = ?
                 ORDER BY eh_padrao DESC LIMIT 1
                """,
                (manter,),
            ).fetchone()
            if var_padrao:
                con.execute("UPDATE produto SET gtin = COALESCE(gtin, ?) WHERE id = ?",
                            (var_padrao["gtin"], manter))
        except sqlite3.OperationalError:
            pass

    log.info("unificado: %r absorveu %r", alvo["descricao_canonica"],
             outro["descricao_canonica"])
    return {"manter": manter, "absorvido": outro["descricao_canonica"]}


def recusar(con: sqlite3.Connection, desc_a: str, desc_b: str) -> None:
    """Marca um par como "não são a mesma coisa", para não sugerir de novo."""
    a, b = sorted((desc_a, desc_b))
    with con:
        con.execute("INSERT OR IGNORE INTO unificacao_recusada (desc_a, desc_b) VALUES (?, ?)",
                    (a, b))


def unificar_como_linha(
    con: sqlite3.Connection,
    mestre_id: int,
    variante_id: int,
    sabor_mestre: str | None = None,
    sabor_variante: str | None = None,
) -> dict:
    """Transforma `variante_id` em um sabor/variante sob o produto `mestre_id`,

    migrando seu histórico de compras com a rotulagem de sabor correspondente.
    """
    if mestre_id == variante_id:
        raise ValueError("não dá para agrupar um produto com ele mesmo")

    alvo = con.execute("SELECT * FROM produto WHERE id = ?", (mestre_id,)).fetchone()
    outro = con.execute("SELECT * FROM produto WHERE id = ?", (variante_id,)).fetchone()
    if not alvo or not outro:
        raise ValueError("produto não encontrado")

    from .catalogo import extrair_sabor

    s_var = (sabor_variante or "").strip()
    if not s_var:
        s_var = (
            extrair_sabor(outro["descricao_canonica"], outro["marca"])
            or outro["descricao_canonica"]
        )

    s_mestre = (sabor_mestre or "").strip() or None
    gtin_var = outro["gtin"] or f"sabor:{variante_id}"

    with con:
        # Se o mestre não tinha variantes mas tinha GTIN, cria a variante padrão para o mestre
        try:
            var_mestre_count = con.execute(
                "SELECT COUNT(*) as c FROM produto_variante WHERE produto_id = ?",
                (mestre_id,),
            ).fetchone()["c"]
            if var_mestre_count == 0 and alvo["gtin"]:
                con.execute(
                    """INSERT OR IGNORE INTO produto_variante (
                           produto_id, gtin, nome, sabor, marca, nutriscore,
                           nova_group, foto_url, eh_padrao, origem, criado_em
                       ) VALUES (
                           ?, ?, ?, ?, ?, ?, ?, ?, 1, 'mestre_original',
                           datetime('now', 'localtime')
                       )""",
                    (
                        mestre_id,
                        alvo["gtin"],
                        alvo["descricao_canonica"],
                        s_mestre,
                        alvo["marca"],
                        alvo["nutriscore"],
                        alvo["nova_group"],
                        alvo["foto_url"],
                    ),
                )
        except sqlite3.OperationalError:
            pass

        # Insere a nova variante correspondente ao produto absorvido
        try:
            con.execute(
                """INSERT OR REPLACE INTO produto_variante (
                       produto_id, gtin, nome, sabor, marca, nutriscore,
                       nova_group, foto_url, eh_padrao, origem, criado_em
                   ) VALUES (
                       ?, ?, ?, ?, ?, ?, ?, ?, 0, 'unificacao_linha',
                       datetime('now', 'localtime')
                   )""",
                (
                    mestre_id,
                    gtin_var,
                    outro["descricao_canonica"],
                    s_var,
                    outro["marca"],
                    outro["nutriscore"],
                    outro["nova_group"],
                    outro["foto_url"],
                ),
            )
            # Transfere quaisquer outras variantes já existentes no produto absorvido
            con.execute(
                "UPDATE OR IGNORE produto_variante SET produto_id = ? WHERE produto_id = ?",
                (mestre_id, variante_id),
            )
            con.execute("DELETE FROM produto_variante WHERE produto_id = ?", (variante_id,))
        except sqlite3.OperationalError:
            pass

        # Atualiza compras do produto absorvido para apontarem para o mestre com o sabor preenchido
        con.execute(
            """UPDATE item
                  SET produto_id = ?,
                      gtin_variante = COALESCE(gtin_variante, ?),
                      sabor_adicional = COALESCE(sabor_adicional, ?)
                WHERE produto_id = ?""",
            (mestre_id, outro["gtin"], s_var, variante_id),
        )

        # Se sabor do mestre foi informado, preenche itens do mestre que estavam sem sabor
        if s_mestre:
            con.execute(
                """UPDATE item
                      SET sabor_adicional = COALESCE(sabor_adicional, ?)
                    WHERE produto_id = ? AND sabor_adicional IS NULL""",
                (s_mestre, mestre_id),
            )

        # Atualiza memória de loja do produto absorvido
        try:
            con.execute(
                """UPDATE OR IGNORE codigo_loja_produto
                      SET produto_id = ?,
                          gtin = COALESCE(gtin, ?),
                          sabor = COALESCE(sabor, ?)
                    WHERE produto_id = ?""",
                (mestre_id, outro["gtin"], s_var, variante_id),
            )
            con.execute("DELETE FROM codigo_loja_produto WHERE produto_id = ?", (variante_id,))
        except sqlite3.OperationalError:
            pass

        # Move preços, apelidos e lista de compras
        con.execute(
            "UPDATE OR IGNORE preco SET produto_id = ? WHERE produto_id = ?",
            (mestre_id, variante_id),
        )
        con.execute("DELETE FROM preco WHERE produto_id = ?", (variante_id,))
        con.execute(
            "UPDATE apelido_loja SET produto_id = ? WHERE produto_id = ?",
            (mestre_id, variante_id),
        )
        con.execute(
            "UPDATE OR IGNORE lista_item SET produto_id = ? WHERE produto_id = ?",
            (mestre_id, variante_id),
        )
        con.execute("DELETE FROM lista_item WHERE produto_id = ?", (variante_id,))

        # Remove o produto absorvido da tabela produto
        con.execute("DELETE FROM produto WHERE id = ?", (variante_id,))

        # Herança de atributos se o mestre não tiver
        campos_heranca = (
            "categoria", "marca", "nutriscore", "nova_group", "foto_url"
        )
        for campo in campos_heranca:
            if campo in alvo.keys() and not alvo[campo] and outro[campo]:
                con.execute(
                    f"UPDATE produto SET {campo} = ? WHERE id = ?",  # noqa: S608
                    (outro[campo], mestre_id),
                )

    log.info(
        "agrupado como linha multissabor: %r adotou %r como sabor %r",
        alvo["descricao_canonica"],
        outro["descricao_canonica"],
        s_var,
    )
    return {
        "mestre_id": mestre_id,
        "variante_id": variante_id,
        "sabor": s_var,
        "absorvido": outro["descricao_canonica"],
    }
