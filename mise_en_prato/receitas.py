#!/usr/bin/env python3
"""Módulo de gestão de receitas culinárias e ingredientes no mise-en-prato.

Permite planejar e registrar receitas da casa integrando os ingredientes com
o histórico de compras e códigos de barras (GTIN/EAN).

Cálculos e quantidades nunca usam ponto flutuante (float):
- No banco: quantidade vive como INTEGER de centavos (escala 100), garantindo
  armazenamento exato e sem perda de precisão.
- No Python: quantidade trafega estritamente como Decimal (ou int),
  permitindo medições exatas (ex: 250 g, 1.5 xícara, 3 unidades).
"""

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


def centavos_quantidade(quantidade: Decimal | int | str) -> int:
    """Converte quantidade Decimal/int/str para centavos inteiros (INTEGER).

    Lança TypeError caso um valor float seja fornecido, assegurando que
    erros de precisão de ponto flutuante sejam impedidos na raiz.
    """
    if isinstance(quantidade, float):
        raise TypeError("Valores float não são permitidos; use Decimal ou int para precisão.")
    if isinstance(quantidade, (int, str, Decimal)):
        dec = Decimal(str(quantidade))
        if dec <= Decimal("0"):
            raise ValueError("A quantidade deve ser estritamente maior que zero.")
        return int((dec * 100).quantize(Decimal("1")))
    raise TypeError(f"Tipo não suportado para quantidade: {type(quantidade).__name__}")


def decimal_quantidade(centavos: int) -> Decimal:
    """Converte centavos inteiros do banco para Decimal sem ponto flutuante.

    Se o valor representar um número inteiro (ex: 25000 -> 250), devolve
    Decimal("250"). Se contiver casas fracionárias (ex: 150 -> 1.5), normaliza
    mantendo a representação decimal enxuta.
    """
    val = Decimal(centavos) / Decimal(100)
    if val == val.to_integral():
        return Decimal(int(val))
    return val.normalize()


@dataclass
class Ingrediente:
    id: int | None
    receita_id: int
    nome_ingrediente: str
    quantidade: Decimal
    unidade: str
    gtin_associado: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.quantidade, float):
            raise TypeError("Floats não são permitidos para quantidade; use Decimal ou int.")
        if not isinstance(self.quantidade, Decimal):
            self.quantidade = Decimal(str(self.quantidade))
        if self.quantidade <= Decimal("0"):
            raise ValueError("A quantidade deve ser maior que zero.")
        self.nome_ingrediente = self.nome_ingrediente.strip()
        if not self.nome_ingrediente:
            raise ValueError("O nome do ingrediente não pode ser vazio.")
        self.unidade = self.unidade.strip()
        if not self.unidade:
            raise ValueError("A unidade não pode ser vazia.")

    @property
    def quantidade_centavos(self) -> int:
        """Retorna a quantidade em centavos inteiros (ex: Decimal('1.5') -> 150)."""
        return int((self.quantidade * 100).quantize(Decimal("1")))

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class Receita:
    id: int | None
    nome: str
    porcoes: int = 1
    tempo_preparo_min: int | None = None
    instrucoes: str | None = None
    criada_em: str = field(
        default_factory=lambda: datetime.now().isoformat(sep=" ", timespec="seconds")
    )
    ingredientes: list[Ingrediente] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.porcoes, float):
            raise TypeError("Porções devem ser um número inteiro, sem float.")
        self.porcoes = int(self.porcoes)
        if self.porcoes <= 0:
            raise ValueError("O número de porções deve ser maior que zero.")

        if self.tempo_preparo_min is not None:
            if isinstance(self.tempo_preparo_min, float):
                raise TypeError("Tempo de preparo deve ser um número inteiro em minutos.")
            self.tempo_preparo_min = int(self.tempo_preparo_min)
            if self.tempo_preparo_min < 0:
                raise ValueError("Tempo de preparo não pode ser negativo.")

        self.nome = self.nome.strip()
        if not self.nome:
            raise ValueError("O nome da receita não pode ser vazio.")

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


def criar_receita(
    con: sqlite3.Connection,
    nome: str | Receita,
    porcoes: int = 1,
    tempo_preparo_min: int | None = None,
    instrucoes: str | None = None,
    criada_em: str | datetime | None = None,
    ingredientes: list[Ingrediente | dict[str, Any]] | None = None,
) -> int:
    """Cria uma nova receita no banco de dados e retorna seu ID."""
    if isinstance(nome, Receita):
        obj = nome
        nome_str = obj.nome
        porcoes = obj.porcoes
        tempo_preparo_min = obj.tempo_preparo_min
        instrucoes = obj.instrucoes
        criada_em_str = obj.criada_em
        ingredientes_lista = obj.ingredientes or ingredientes or []
    else:
        if isinstance(porcoes, float) or isinstance(tempo_preparo_min, float):
            raise TypeError("Valores numéricos de porções e tempo não podem ser float.")
        nome_str = str(nome).strip()
        if not nome_str:
            raise ValueError("O nome da receita não pode ser vazio.")
        if porcoes <= 0:
            raise ValueError("O número de porções deve ser maior que zero.")
        if tempo_preparo_min is not None and tempo_preparo_min < 0:
            raise ValueError("Tempo de preparo não pode ser negativo.")

        if criada_em is None:
            criada_em_str = datetime.now().isoformat(sep=" ", timespec="seconds")
        elif isinstance(criada_em, datetime):
            criada_em_str = criada_em.isoformat(sep=" ", timespec="seconds")
        else:
            criada_em_str = str(criada_em)
        ingredientes_lista = ingredientes or []

    with con:
        cursor = con.execute(
            """
            INSERT INTO receitas (nome, porcoes, tempo_preparo_min, instrucoes, criada_em)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                nome_str,
                porcoes,
                tempo_preparo_min,
                instrucoes,
                criada_em_str,
            ),
        )
        receita_id = cursor.lastrowid
        if receita_id is None:
            raise RuntimeError("Falha ao obter ID da receita criada.")

        for item in ingredientes_lista:
            if isinstance(item, Ingrediente):
                adicionar_ingrediente(
                    con,
                    receita_id,
                    item.nome_ingrediente,
                    item.quantidade,
                    item.unidade,
                    item.gtin_associado,
                )
            elif isinstance(item, dict):
                adicionar_ingrediente(
                    con,
                    receita_id,
                    item["nome_ingrediente"],
                    item["quantidade"],
                    item["unidade"],
                    item.get("gtin_associado"),
                )

    return receita_id


def adicionar_ingrediente(
    con: sqlite3.Connection,
    receita_id: int,
    nome_ingrediente: str | Ingrediente,
    quantidade: Decimal | int | str | None = None,
    unidade: str | None = None,
    gtin_associado: str | None = None,
) -> int:
    """Adiciona um ingrediente a uma receita existente."""
    if isinstance(nome_ingrediente, Ingrediente):
        ing = nome_ingrediente
        nome_str = ing.nome_ingrediente
        qtd_centavos = ing.quantidade_centavos
        unidade_str = ing.unidade
        gtin = ing.gtin_associado
    else:
        if quantidade is None:
            raise ValueError("A quantidade do ingrediente deve ser informada.")
        if unidade is None:
            raise ValueError("A unidade do ingrediente deve ser informada.")

        nome_str = str(nome_ingrediente).strip()
        if not nome_str:
            raise ValueError("O nome do ingrediente não pode ser vazio.")
        unidade_str = str(unidade).strip()
        if not unidade_str:
            raise ValueError("A unidade não pode ser vazia.")

        qtd_centavos = centavos_quantidade(quantidade)
        gtin = gtin_associado.strip() if gtin_associado else None

    with con:
        cursor = con.execute(
            """
            INSERT INTO ingredientes (
                receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (receita_id, nome_str, qtd_centavos, unidade_str, gtin),
        )
        ingrediente_id = cursor.lastrowid
        if ingrediente_id is None:
            raise RuntimeError("Falha ao registrar ingrediente.")
        return ingrediente_id


def obter_receita(con: sqlite3.Connection, receita_id: int) -> Receita | None:
    """Obtém uma receita por ID acompanhada da lista completa de ingredientes."""
    row = con.execute(
        """
        SELECT id, nome, porcoes, tempo_preparo_min, instrucoes, criada_em
          FROM receitas
         WHERE id = ?
        """,
        (receita_id,),
    ).fetchone()

    if row is None:
        return None

    ing_rows = con.execute(
        """
        SELECT id, receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
          FROM ingredientes
         WHERE receita_id = ?
         ORDER BY id ASC
        """,
        (receita_id,),
    ).fetchall()

    ingredientes = [
        Ingrediente(
            id=i["id"],
            receita_id=i["receita_id"],
            nome_ingrediente=i["nome_ingrediente"],
            quantidade=decimal_quantidade(i["quantidade"]),
            unidade=i["unidade"],
            gtin_associado=i["gtin_associado"],
        )
        for i in ing_rows
    ]

    return Receita(
        id=row["id"],
        nome=row["nome"],
        porcoes=row["porcoes"],
        tempo_preparo_min=row["tempo_preparo_min"],
        instrucoes=row["instrucoes"],
        criada_em=row["criada_em"],
        ingredientes=ingredientes,
    )


def listar_receitas(con: sqlite3.Connection) -> list[Receita]:
    """Lista todas as receitas cadastradas com seus respectivos ingredientes."""
    rec_rows = con.execute(
        """
        SELECT id, nome, porcoes, tempo_preparo_min, instrucoes, criada_em
          FROM receitas
         ORDER BY criada_em DESC, id DESC
        """
    ).fetchall()

    if not rec_rows:
        return []

    ing_rows = con.execute(
        """
        SELECT id, receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
          FROM ingredientes
         ORDER BY id ASC
        """
    ).fetchall()

    ingredientes_por_receita: dict[int, list[Ingrediente]] = defaultdict(list)
    for i in ing_rows:
        ingredientes_por_receita[i["receita_id"]].append(
            Ingrediente(
                id=i["id"],
                receita_id=i["receita_id"],
                nome_ingrediente=i["nome_ingrediente"],
                quantidade=decimal_quantidade(i["quantidade"]),
                unidade=i["unidade"],
                gtin_associado=i["gtin_associado"],
            )
        )

    return [
        Receita(
            id=r["id"],
            nome=r["nome"],
            porcoes=r["porcoes"],
            tempo_preparo_min=r["tempo_preparo_min"],
            instrucoes=r["instrucoes"],
            criada_em=r["criada_em"],
            ingredientes=ingredientes_por_receita[r["id"]],
        )
        for r in rec_rows
    ]


def buscar_receitas(con: sqlite3.Connection, termo: str) -> list[Receita]:
    """Busca receitas pelo nome correspondente."""
    termo = termo.strip()
    if not termo:
        return listar_receitas(con)

    padrao = f"%{termo}%"
    rec_rows = con.execute(
        """
        SELECT id, nome, porcoes, tempo_preparo_min, instrucoes, criada_em
          FROM receitas
         WHERE nome LIKE ?
         ORDER BY nome ASC
        """,
        (padrao,),
    ).fetchall()

    if not rec_rows:
        return []

    ids = [r["id"] for r in rec_rows]
    placeholders = ",".join("?" * len(ids))
    ing_rows = con.execute(
        f"""
        SELECT id, receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
          FROM ingredientes
         WHERE receita_id IN ({placeholders})
         ORDER BY id ASC
        """,
        ids,
    ).fetchall()

    ingredientes_por_receita: dict[int, list[Ingrediente]] = defaultdict(list)
    for i in ing_rows:
        ingredientes_por_receita[i["receita_id"]].append(
            Ingrediente(
                id=i["id"],
                receita_id=i["receita_id"],
                nome_ingrediente=i["nome_ingrediente"],
                quantidade=decimal_quantidade(i["quantidade"]),
                unidade=i["unidade"],
                gtin_associado=i["gtin_associado"],
            )
        )

    return [
        Receita(
            id=r["id"],
            nome=r["nome"],
            porcoes=r["porcoes"],
            tempo_preparo_min=r["tempo_preparo_min"],
            instrucoes=r["instrucoes"],
            criada_em=r["criada_em"],
            ingredientes=ingredientes_por_receita[r["id"]],
        )
        for r in rec_rows
    ]


def excluir_receita(con: sqlite3.Connection, receita_id: int) -> bool:
    """Exclui uma receita. Os ingredientes são removidos em cascata (FK CASCADE)."""
    with con:
        cur = con.execute("DELETE FROM receitas WHERE id = ?", (receita_id,))
        return cur.rowcount > 0


def remover_ingrediente(con: sqlite3.Connection, ingrediente_id: int) -> bool:
    """Remove um ingrediente específico por ID."""
    with con:
        cur = con.execute("DELETE FROM ingredientes WHERE id = ?", (ingrediente_id,))
        return cur.rowcount > 0


def escalar_receita(receita: Receita, novas_porcoes: int) -> Receita:
    """Escala as quantidades dos ingredientes para novas porções usando Decimal puro.

    Sem aproximações binárias de float: o fator de escala é calculado
    como razão exata e aplicado com precisão decimal.
    """
    if isinstance(novas_porcoes, float):
        raise TypeError("O número de novas porções deve ser um inteiro, sem float.")
    if novas_porcoes <= 0:
        raise ValueError("O número de novas porções deve ser maior que zero.")
    if receita.porcoes <= 0:
        raise ValueError("A receita de origem deve ter porções maiores que zero.")

    fator = Decimal(novas_porcoes) / Decimal(receita.porcoes)
    novos_ingredientes = []

    for ing in receita.ingredientes:
        nova_qtd = (ing.quantidade * fator).quantize(Decimal("0.01"))
        if nova_qtd == nova_qtd.to_integral():
            nova_qtd_final = Decimal(int(nova_qtd))
        else:
            nova_qtd_final = nova_qtd.normalize()

        novos_ingredientes.append(
            Ingrediente(
                id=ing.id,
                receita_id=ing.receita_id,
                nome_ingrediente=ing.nome_ingrediente,
                quantidade=nova_qtd_final,
                unidade=ing.unidade,
                gtin_associado=ing.gtin_associado,
            )
        )

    return Receita(
        id=receita.id,
        nome=receita.nome,
        porcoes=novas_porcoes,
        tempo_preparo_min=receita.tempo_preparo_min,
        instrucoes=receita.instrucoes,
        criada_em=receita.criada_em,
        ingredientes=novos_ingredientes,
    )
