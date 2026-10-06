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
                produto_id = _produto_de(
                    con, cupom.estabelecimento.cnpj, item.descricao,
                    unidade=item.unidade, quantidade=item.quantidade, codigo_loja=item.codigo_loja,
                )
                con.execute(
                    """INSERT INTO item (chave, seq, descricao, codigo_loja, unidade,
                                         quantidade_milesimos, unitario_centavos, total_centavos,
                                         produto_id)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (cupom.chave, item.seq, item.descricao, item.codigo_loja, item.unidade,
                     milesimos(item.quantidade), centavos(item.valor_unitario),
                     centavos(item.valor_total), produto_id),
                )
                if item.gtin or item.ncm:
                    con.execute(
                        """UPDATE produto
                              SET gtin = COALESCE(gtin, ?), ncm = COALESCE(ncm, ?)
                            WHERE id = ?""",
                        (item.gtin, item.ncm, produto_id),
                    )
                con.execute(
                    """INSERT OR IGNORE INTO preco
                           (produto_id, cnpj, unitario_centavos, observado_em, fonte)
                       VALUES (?,?,?,?,'cupom')""",
                    (produto_id, cupom.estabelecimento.cnpj, centavos(item.valor_unitario),
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
            produto_id = _produto_de(
                con, cupom.estabelecimento.cnpj, item.descricao,
                unidade=item.unidade, quantidade=item.quantidade, codigo_loja=item.codigo_loja,
            )
            con.execute(
                """INSERT INTO item (chave, seq, descricao, codigo_loja, unidade,
                                     quantidade_milesimos, unitario_centavos, total_centavos,
                                     produto_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (cupom.chave, item.seq, item.descricao, item.codigo_loja, item.unidade,
                 milesimos(item.quantidade), centavos(item.valor_unitario),
                 centavos(item.valor_total), produto_id),
            )
            if item.gtin or item.ncm:
                con.execute(
                    """UPDATE produto
                          SET gtin = COALESCE(gtin, ?), ncm = COALESCE(ncm, ?)
                        WHERE id = ?""",
                    (item.gtin, item.ncm, produto_id),
                )
            # O preço pago entra na MESMA série que o Menor Preço alimenta.
            # OR IGNORE porque duas linhas idênticas do mesmo cupom (o atum
            # comprado duas vezes) são uma observação de preço só.
            con.execute(
                """INSERT OR IGNORE INTO preco
                       (produto_id, cnpj, unitario_centavos, observado_em, fonte)
                   VALUES (?,?,?,?,'cupom')""",
                (produto_id, cupom.estabelecimento.cnpj, centavos(item.valor_unitario),
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

    itens = [
        {"seq": i["seq"], "descricao": i["descricao"], "unidade": i["unidade"],
         "quantidade": unidades(i["quantidade_milesimos"]),
         "valor_unitario": reais(i["unitario_centavos"]),
         "valor_total": reais(i["total_centavos"]), "produto_id": i["produto_id"]}
        for i in con.execute("SELECT * FROM item WHERE chave = ? ORDER BY seq", (chave,))
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
