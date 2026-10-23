#!/usr/bin/env python3
"""Gravação e leitura de cupom no banco.

Reenviar o mesmo cupom não duplica nada: a chave de acesso é a chave primária,
e quem já está gravado volta como está. É de propósito — a mesma foto mandada
duas vezes é o caso comum, não a exceção.
"""
import json
import re
import sqlite3
from datetime import datetime
from decimal import Decimal

from .banco import centavos, milesimos, reais, unidades
from .nfce_pr import Cupom, normalizar


def eh_granel(descricao: str, unidade: str = "", quantidade: Decimal | None = None,
              codigo_loja: str = "") -> bool:
    """Reconhece se o produto é vendido a granel / pesado na balança.

    Produtos a granel nunca têm código de barras comercial (GTIN/EAN).
    Sinais confiáveis:
      - unidade de peso no cupom: KG, GR, G, QUILO, KG., KGS
      - menção explícita a KG ou GRANEL na descrição (ex: 'CEBOLA NACIONAL KG')
      - código interno de balança da loja (iniciado com dígito 2 e tamanho EAN 12/13)
      - quantidade fracionária com unidade não-unitária
    """
    u = (unidade or "").strip().upper()
    if u in ("KG", "GR", "G", "QUILO", "KG.", "KGS"):
        return True

    desc = normalizar(descricao)
    if re.search(r"\b(KG|QUILO|A GRANEL|GRANEL|PESO)\b", desc):
        return True

    cod = re.sub(r"\D", "", codigo_loja or "")
    if len(cod) in (12, 13) and cod.startswith("2"):
        return True

    if quantidade is not None and (quantidade % 1 != 0):
        if u not in ("UN", "PC", "CX", "PCT", "PT", "FR", "L", "LT", "ML"):
            return True

    return False


def existe(con: sqlite3.Connection, chave: str) -> bool:
    return con.execute("SELECT 1 FROM cupom WHERE chave = ?", (chave,)).fetchone() is not None


def _garantir_estabelecimento(con: sqlite3.Connection, e) -> None:
    if not e or not getattr(e, "cnpj", None) or not e.cnpj.strip():
        return
    con.execute(
        """INSERT INTO estabelecimento (cnpj, razao, logradouro, numero, complemento,
                                        bairro, municipio, uf)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cnpj) DO UPDATE SET razao = excluded.razao""",
        (e.cnpj, e.razao, e.logradouro, e.numero, e.complemento, e.bairro, e.municipio, e.uf),
    )


def _produto_de(con: sqlite3.Connection, cnpj: str, descricao: str, *,
                unidade: str = "", quantidade: Decimal | None = None,
                codigo_loja: str = "") -> int:
    """Descrição impressa pela loja -> id de produto. Os dois primeiros degraus.

    1. `apelido_loja`: se (loja, texto) já foi resolvido, acabou — é o que faz o
       segundo cupom da mesma loja não consultar nada.
    2. senão, o produto passa a ser identificado pela descrição normalizada.

    Duas lojas que imprimem exatamente "IOG FRIMESA PROT BAUNILHA 170G" estão
    quase certamente falando do mesmo item, então a descrição normalizada é
    global. O GTIN, quando o Menor Preço o entregar, vira a identidade de
    verdade; até lá o produto existe e já acumula o SEU histórico de preço.
    """
    norm = normalizar(descricao)
    granel = 1 if eh_granel(descricao, unidade, quantidade, codigo_loja) else 0

    achado = con.execute(
        "SELECT produto_id FROM apelido_loja WHERE cnpj = ? AND descricao_norm = ?", (cnpj, norm)
    ).fetchone()
    if achado:
        produto_id = achado["produto_id"]
        if granel:
            con.execute(
                "UPDATE produto SET is_granel = 1 WHERE id = ? AND is_granel = 0", (produto_id,)
            )
        return produto_id

    achado = con.execute(
        "SELECT id, is_granel FROM produto WHERE descricao_canonica = ?", (norm,)
    ).fetchone()
    if achado:
        produto_id = achado["id"]
        if granel and not achado["is_granel"]:
            con.execute("UPDATE produto SET is_granel = 1 WHERE id = ?", (produto_id,))
    else:
        produto_id = con.execute(
            "INSERT INTO produto (descricao_canonica, is_granel) VALUES (?, ?)", (norm, granel)
        ).lastrowid

    con.execute(
        "INSERT OR IGNORE INTO apelido_loja (cnpj, descricao_norm, produto_id) VALUES (?, ?, ?)",
        (cnpj, norm, produto_id),
    )
    return produto_id


def _resolver_item_produto(
    con: sqlite3.Connection,
    cnpj: str,
    descricao: str,
    *,
    codigo_loja: str = "",
    unidade: str = "",
    quantidade: Decimal | None = None,
    gtin_item: str | None = None,
) -> tuple[int, str | None, str | None]:
    """Resolve o produto_id, gtin_variante e sabor_adicional do item.

    1. Primeiro verifica se a loja já tem mapeamento prévio em codigo_loja_produto.
    2. Se não houver, infere o produto por descrição e associa variantes conhecidas.
    """
    cod_loja_str = str(codigo_loja or "").strip()
    if cod_loja_str:
        try:
            mapa = con.execute(
                """SELECT produto_id, gtin, sabor
                     FROM codigo_loja_produto
                    WHERE cnpj = ? AND codigo_loja = ?""",
                (cnpj, cod_loja_str),
            ).fetchone()
            if mapa:
                sabor = mapa["sabor"]
                gtin = mapa["gtin"]
                if not sabor and gtin:
                    var = con.execute(
                        "SELECT sabor FROM produto_variante WHERE produto_id = ? AND gtin = ?",
                        (mapa["produto_id"], gtin),
                    ).fetchone()
                    if var and var["sabor"]:
                        sabor = var["sabor"]
                return mapa["produto_id"], gtin, sabor
        except sqlite3.OperationalError:
            pass

    produto_id = _produto_de(
        con,
        cnpj,
        descricao,
        unidade=unidade,
        quantidade=quantidade,
        codigo_loja=codigo_loja,
    )
    gtin_variante = gtin_item or None
    sabor_adicional = None
    if gtin_variante:
        try:
            var = con.execute(
                "SELECT sabor FROM produto_variante WHERE produto_id = ? AND gtin = ?",
                (produto_id, gtin_variante),
            ).fetchone()
            if var and var["sabor"]:
                sabor_adicional = var["sabor"]
        except sqlite3.OperationalError:
            pass

    return produto_id, gtin_variante, sabor_adicional


def _tentar_enriquecimento_local(con: sqlite3.Connection, produto_id: int) -> None:
    """Tenta enriquecer o produto a partir do catálogo satélite offline, se disponível."""
    try:
        from .enriquecimento import enriquecer_local
        enriquecer_local(con, produto_id)
    except Exception:
        pass


def foto_de_falha_proxima(criado_em_str: str | None) -> str | None:
    """Acha a foto em falhas/ mais próxima do momento em que o cupom foi criado."""
    if not criado_em_str:
        return None
    try:
        dt_cupom = datetime.fromisoformat(criado_em_str)
    except (ValueError, TypeError):
        return None
    from .config import config

    pasta = config().dados / "falhas"
    if not pasta.is_dir():
        return None
    melhor_foto = None
    menor_delta = 900  # até 15 minutos de diferença
    for arq in pasta.glob("*.jpg"):
        try:
            dt_foto = datetime.strptime(arq.stem, "%Y-%m-%d_%H%M%S")
            delta = abs((dt_cupom - dt_foto).total_seconds())
            if delta < menor_delta:
                menor_delta = delta
                melhor_foto = arq.name
        except ValueError:
            continue
    return melhor_foto


def gravar(con: sqlite3.Connection, cupom: Cupom, *, morador: str,
           origem: str = "qrcode", url_qr: str = "") -> bool:
    """Grava o cupom inteiro numa transação. Devolve True se era novo ou completou."""
    linha_existente = con.execute(
        "SELECT completo FROM cupom WHERE chave = ?", (cupom.chave,)
    ).fetchone()

    if linha_existente:
        if linha_existente["completo"]:
            return False
        # Já existia, mas era incompleto: atualiza com os novos dados
        with con:
            _garantir_estabelecimento(con, cupom.estabelecimento)
            con.execute("DELETE FROM item WHERE chave = ?", (cupom.chave,))
            con.execute(
                """UPDATE cupom SET
                       cnpj = ?, emitido_em = ?, numero = ?, serie = ?, protocolo = ?,
                       total_centavos = ?, desconto_centavos = ?, pago_centavos = ?,
                       forma_pagamento = ?, consumidor = ?, morador = ?, origem = ?,
                       completo = ?, avisos = ?, url_qr = COALESCE(NULLIF(?, ''), url_qr)
                 WHERE chave = ?""",
                (cupom.estabelecimento.cnpj,
                 cupom.emitido_em.isoformat(sep=" ") if cupom.emitido_em else None,
                 cupom.numero, cupom.serie, cupom.protocolo,
                 centavos(cupom.total), centavos(cupom.desconto) or 0, centavos(cupom.valor_pago),
                 cupom.forma_pagamento, cupom.consumidor, morador, origem,
                 1 if cupom.completo else 0,
                 json.dumps(cupom.avisos, ensure_ascii=False) if cupom.avisos else None,
                 url_qr, cupom.chave),
            )
            for item in cupom.itens:
                produto_id, gtin_var, sabor_adic = _resolver_item_produto(
                    con,
                    cupom.estabelecimento.cnpj,
                    item.descricao,
                    codigo_loja=item.codigo_loja,
                    unidade=item.unidade,
                    quantidade=item.quantidade,
                    gtin_item=getattr(item, "gtin", None),
                )
                con.execute(
                    """INSERT INTO item (chave, seq, descricao, codigo_loja, unidade,
                                         quantidade_milesimos, unitario_centavos, total_centavos,
                                         produto_id, gtin_variante, sabor_adicional)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (cupom.chave, item.seq, item.descricao, item.codigo_loja, item.unidade,
                     milesimos(item.quantidade), centavos(item.valor_unitario),
                     centavos(item.valor_total), produto_id, gtin_var, sabor_adic),
                )
                if item.gtin or item.ncm:
                    con.execute(
                        """UPDATE produto
                              SET gtin = COALESCE(gtin, ?), ncm = COALESCE(ncm, ?)
                            WHERE id = ?""",
                        (item.gtin, item.ncm, produto_id),
                    )
                _tentar_enriquecimento_local(con, produto_id)
                e = cupom.estabelecimento
                if e and getattr(e, "cnpj", None) and e.cnpj.strip():
                    con.execute(
                        """INSERT OR IGNORE INTO preco
                               (produto_id, cnpj, unitario_centavos, observado_em, fonte)
                           VALUES (?,?,?,?,'cupom')""",
                        (produto_id, e.cnpj, centavos(item.valor_unitario),
                         cupom.emitido_em.isoformat(sep=" ") if cupom.emitido_em else None),
                    )
        return True

    with con:  # transação: ou entra o cupom todo, ou não entra nada
        _garantir_estabelecimento(con, cupom.estabelecimento)
        con.execute(
            """INSERT INTO cupom (chave, cnpj, emitido_em, numero, serie, protocolo,
                                  total_centavos, desconto_centavos, pago_centavos,
                                  forma_pagamento, consumidor, morador, origem, completo,
                                  avisos, url_qr, criado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cupom.chave, cupom.estabelecimento.cnpj,
             cupom.emitido_em.isoformat(sep=" ") if cupom.emitido_em else None,
             cupom.numero, cupom.serie, cupom.protocolo,
             centavos(cupom.total), centavos(cupom.desconto) or 0, centavos(cupom.valor_pago),
             cupom.forma_pagamento, cupom.consumidor, morador, origem,
             1 if cupom.completo else 0,
             json.dumps(cupom.avisos, ensure_ascii=False) if cupom.avisos else None,
             url_qr, datetime.now().isoformat(sep=" ", timespec="seconds")),
        )
        for item in cupom.itens:
            produto_id, gtin_var, sabor_adic = _resolver_item_produto(
                con,
                cupom.estabelecimento.cnpj,
                item.descricao,
                codigo_loja=item.codigo_loja,
                unidade=item.unidade,
                quantidade=item.quantidade,
                gtin_item=getattr(item, "gtin", None),
            )
            con.execute(
                """INSERT INTO item (chave, seq, descricao, codigo_loja, unidade,
                                     quantidade_milesimos, unitario_centavos, total_centavos,
                                     produto_id, gtin_variante, sabor_adicional)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (cupom.chave, item.seq, item.descricao, item.codigo_loja, item.unidade,
                 milesimos(item.quantidade), centavos(item.valor_unitario),
                 centavos(item.valor_total), produto_id, gtin_var, sabor_adic),
            )
            if item.gtin or item.ncm:
                con.execute(
                    """UPDATE produto
                          SET gtin = COALESCE(gtin, ?), ncm = COALESCE(ncm, ?)
                        WHERE id = ?""",
                    (item.gtin, item.ncm, produto_id),
                )
            _tentar_enriquecimento_local(con, produto_id)
            # O preço pago entra na MESMA série que o Menor Preço alimenta.
            # OR IGNORE porque duas linhas idênticas do mesmo cupom (o atum
            # comprado duas vezes) são uma observação de preço só.
            e = cupom.estabelecimento
            if e and getattr(e, "cnpj", None) and e.cnpj.strip():
                con.execute(
                    """INSERT OR IGNORE INTO preco
                           (produto_id, cnpj, unitario_centavos, observado_em, fonte)
                       VALUES (?,?,?,?,'cupom')""",
                    (produto_id, e.cnpj, centavos(item.valor_unitario),
                     cupom.emitido_em.isoformat(sep=" ") if cupom.emitido_em else None),
                )
    return True


def ler(con: sqlite3.Connection, chave: str) -> dict | None:
    """O cupom como a tela quer: valores em Decimal, itens em ordem."""
    linha = con.execute(
        """SELECT c.*, e.razao, e.municipio, e.bairro
             FROM cupom c JOIN estabelecimento e ON e.cnpj = c.cnpj
            WHERE c.chave = ?""", (chave,)).fetchone()
    if not linha:
        return None

    linhas_itens = list(con.execute("SELECT * FROM item WHERE chave = ? ORDER BY seq", (chave,)))
    pids = {i["produto_id"] for i in linhas_itens if i["produto_id"]}
    variantes_por_prod: dict[int, list[dict]] = {}
    if pids:
        try:
            placeholders = ",".join("?" for _ in pids)
            for v in con.execute(
                f"""SELECT produto_id, gtin, nome, sabor, marca, nutriscore,
                           nova_group, foto_url, eh_padrao
                      FROM produto_variante
                     WHERE produto_id IN ({placeholders})
                     ORDER BY eh_padrao DESC, sabor, nome""",
                list(pids),
            ):
                variantes_por_prod.setdefault(v["produto_id"], []).append(dict(v))
        except sqlite3.OperationalError:
            pass

    itens = [
        {
            "id": i["id"],
            "seq": i["seq"],
            "descricao": i["descricao"],
            "codigo_loja": i["codigo_loja"],
            "unidade": i["unidade"],
            "quantidade": unidades(i["quantidade_milesimos"]),
            "valor_unitario": reais(i["unitario_centavos"]),
            "valor_total": reais(i["total_centavos"]),
            "produto_id": i["produto_id"],
            "gtin_variante": i["gtin_variante"],
            "sabor_adicional": i["sabor_adicional"],
            "variantes": variantes_por_prod.get(i["produto_id"], []),
        }
        for i in linhas_itens
    ]
    return {
        "chave": linha["chave"], "loja": linha["razao"], "municipio": linha["municipio"],
        "emitido_em": linha["emitido_em"], "morador": linha["morador"],
        "total": reais(linha["total_centavos"]),
        "desconto": reais(linha["desconto_centavos"]),
        "valor_pago": reais(linha["pago_centavos"]),
        "forma_pagamento": linha["forma_pagamento"],
        "completo": bool(linha["completo"]),
        "avisos": json.loads(linha["avisos"]) if linha["avisos"] else [],
        "foto": foto_de_falha_proxima(linha["criado_em"]) if not bool(linha["completo"]) else None,
        "itens": itens,
    }


def fila_de_revisao(con: sqlite3.Connection) -> list[dict]:
    """Cupons que entraram pela metade e esperam um olho humano."""
    return [
        {"chave": r["chave"], "loja": r["razao"], "cnpj": r["cnpj"],
         "numero": r["numero"], "serie": r["serie"], "morador": r["morador"],
         "criado_em": r["criado_em"], "emitido_em": r["emitido_em"],
         "valor_pago": reais(r["pago_centavos"]),
         "avisos": json.loads(r["avisos"]) if r["avisos"] else [],
         "foto": foto_de_falha_proxima(r["criado_em"])}
        for r in con.execute(
            """SELECT c.chave, c.emitido_em, c.criado_em, c.numero, c.serie,
                      c.morador, c.pago_centavos, c.avisos, e.razao, e.cnpj
                 FROM cupom c JOIN estabelecimento e ON e.cnpj = c.cnpj
                WHERE c.completo = 0 ORDER BY c.criado_em DESC""")
    ]


def recentes(con: sqlite3.Connection, limite: int = 20) -> list[dict]:
    return [
        {"chave": r["chave"], "loja": r["razao"], "emitido_em": r["emitido_em"],
         "morador": r["morador"], "valor_pago": reais(r["pago_centavos"]),
         "itens": r["itens"], "completo": bool(r["completo"])}
        for r in con.execute(
            """SELECT c.chave, c.emitido_em, c.morador, c.pago_centavos, c.completo, e.razao,
                      (SELECT COUNT(*) FROM item i WHERE i.chave = c.chave) AS itens
                 FROM cupom c JOIN estabelecimento e ON e.cnpj = c.cnpj
                ORDER BY c.emitido_em DESC LIMIT ?""", (limite,))
    ]


def total_conferido(cupom: Cupom) -> Decimal:
    """Soma dos itens menos o desconto — o número que tem de bater com o pago."""
    return sum((i.valor_total for i in cupom.itens), Decimal("0")) - cupom.desconto


def revisar(con: sqlite3.Connection, chave: str, novos_itens: list[dict], *,
            total: Decimal | None = None, desconto: Decimal | None = None,
            valor_pago: Decimal | None = None, aprovar: bool = False) -> bool:
    """Atualiza itens e valores de um cupom em revisão, permitindo aprovação humana."""
    linha = con.execute("SELECT * FROM cupom WHERE chave = ?", (chave,)).fetchone()
    if not linha:
        return False

    itens_parseados = []
    seq = 1
    for it in novos_itens:
        desc = it.get("descricao", "").strip()
        if not desc:
            continue
        qtd = Decimal(str(it.get("quantidade", 1)))
        unit = Decimal(str(it.get("valor_unitario", 0)))
        tot = Decimal(str(it.get("valor_total", (qtd * unit).quantize(Decimal("0.01")))))
        unidade = (it.get("unidade") or "UN").strip().upper()
        codigo_loja = str(it.get("codigo_loja") or "")
        itens_parseados.append({
            "seq": seq, "descricao": desc, "quantidade": qtd, "unidade": unidade,
            "valor_unitario": unit, "valor_total": tot, "codigo_loja": codigo_loja,
            "gtin": it.get("gtin"), "ncm": it.get("ncm")
        })
        seq += 1

    soma_itens = sum((it["valor_total"] for it in itens_parseados), Decimal("0"))
    desc = desconto if desconto is not None else (reais(linha["desconto_centavos"]) or Decimal("0"))
    tot_efetivo = total if total is not None else (soma_itens if itens_parseados else Decimal("0"))
    pago_efetivo = valor_pago if valor_pago is not None else (tot_efetivo - desc)

    avisos = []
    if not itens_parseados:
        avisos.append("Cupom sem itens")
    else:
        diferenca = abs((soma_itens - desc) - pago_efetivo)
        if diferenca > Decimal("0.02"):
            avisos.append(
                f"Divergência de centavos: soma dos itens (R$ {soma_itens}) - "
                f"desconto (R$ {desc}) != pago (R$ {pago_efetivo})"
            )

    if aprovar:
        completo = 1
        avisos = []
    else:
        completo = 1 if not avisos else 0

    with con:
        con.execute("DELETE FROM item WHERE chave = ?", (chave,))
        for it in itens_parseados:
            produto_id, gtin_var, sabor_adic = _resolver_item_produto(
                con,
                linha["cnpj"],
                it["descricao"],
                codigo_loja=it["codigo_loja"],
                unidade=it["unidade"],
                quantidade=it["quantidade"],
                gtin_item=it.get("gtin"),
            )
            con.execute(
                """INSERT INTO item (chave, seq, descricao, codigo_loja, unidade,
                                     quantidade_milesimos, unitario_centavos, total_centavos,
                                     produto_id, gtin_variante, sabor_adicional)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (chave, it["seq"], it["descricao"], it["codigo_loja"], it["unidade"],
                 milesimos(it["quantidade"]), centavos(it["valor_unitario"]),
                 centavos(it["valor_total"]), produto_id, gtin_var, sabor_adic),
            )
            if it["gtin"] or it["ncm"]:
                con.execute(
                    """UPDATE produto
                          SET gtin = COALESCE(gtin, ?), ncm = COALESCE(ncm, ?)
                        WHERE id = ?""",
                    (it["gtin"], it["ncm"], produto_id)
                )
            _tentar_enriquecimento_local(con, produto_id)
            con.execute(
                """INSERT OR IGNORE INTO preco
                       (produto_id, cnpj, unitario_centavos, observado_em, fonte)
                   VALUES (?,?,?,?,'cupom')""",
                (produto_id, linha["cnpj"], centavos(it["valor_unitario"]), linha["emitido_em"])
            )

        con.execute(
            """UPDATE cupom SET
                   total_centavos = ?, desconto_centavos = ?, pago_centavos = ?,
                   completo = ?, avisos = ?
             WHERE chave = ?""",
            (centavos(tot_efetivo), centavos(desc) or 0, centavos(pago_efetivo),
             completo, json.dumps(avisos, ensure_ascii=False) if avisos else None, chave)
        )

    return True
