#!/usr/bin/env python3
"""Banco sqlite: conexão, migrações e conversão de dinheiro.

Um processo só escreve neste banco — é por isso que o vigia de preços roda como
tarefa de fundo do próprio servidor, e não em contêiner separado. Dois
escritores em sqlite é o caminho curto para "database is locked".

Dinheiro trafega como Decimal no python e vive como INTEGER de centavos no
banco. Não há float em nenhum ponto do caminho: soma de gasto tem de fechar no
centavo. Quantidade segue a mesma ideia, em milésimos, porque o cupom pesa com
três casas ("0,256 KG").
"""
import sqlite3
from decimal import Decimal
from pathlib import Path

MIGRACOES = Path(__file__).parent / "migracoes"


# ── dinheiro e quantidade ────────────────────────────────────────────────────
def centavos(valor: Decimal | int | float | None) -> int | None:
    """Decimal("10.97") -> 1097. Arredonda meio para cima, como caixa faz."""
    if valor is None:
        return None
    return int((Decimal(str(valor)) * 100).quantize(Decimal("1")))


def reais(valor: int | None) -> Decimal | None:
    """1097 -> Decimal("10.97")."""
    return None if valor is None else (Decimal(valor) / 100).quantize(Decimal("0.01"))


def milesimos(quantidade: Decimal | None) -> int | None:
    """Decimal("0.256") -> 256."""
    if quantidade is None:
        return None
    return int((Decimal(str(quantidade)) * 1000).quantize(Decimal("1")))


def unidades(quantidade: int | None) -> Decimal | None:
    """256 -> Decimal("0.256")."""
    return None if quantidade is None else (Decimal(quantidade) / 1000).quantize(Decimal("0.001"))


# ── conexão ──────────────────────────────────────────────────────────────────
def conectar(caminho: Path) -> sqlite3.Connection:
    """Abre o banco já migrado, em WAL e com chave estrangeira ligada.

    WAL porque a tarefa de fundo escreve enquanto a tela lê; sem ele, o vigia de
    preços travaria a navegação a cada gravação.
    """
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    migrar(con)
    return con


def migrar(con: sqlite3.Connection) -> list[str]:
    """Aplica em ordem as migrações ainda não aplicadas. Devolve o que rodou.

    Migração é arquivo numerado em migracoes/, nunca editado depois de aplicado
    — banco em produção só anda para frente.
    """
    # Chave estrangeira desligada durante a migração: reconstruir tabela em
    # sqlite (a única forma de tirar uma constraint de coluna) passa por DROP,
    # e com FK ligada o DROP derruba ou recusa. É o procedimento que a própria
    # documentação do sqlite recomenda. Volta a ligar no fim, sempre.
    con.execute("PRAGMA foreign_keys = OFF")
    con.execute("CREATE TABLE IF NOT EXISTS migracao (nome TEXT PRIMARY KEY, aplicada_em TEXT)")
    ja = {linha["nome"] for linha in con.execute("SELECT nome FROM migracao")}
    aplicadas = []
    for arquivo in sorted(MIGRACOES.glob("*.sql")):
        if arquivo.name in ja:
            continue
        con.executescript(arquivo.read_text(encoding="utf-8"))
        con.execute(
            "INSERT INTO migracao (nome, aplicada_em) VALUES (?, datetime('now'))", (arquivo.name,)
        )
        con.commit()
        aplicadas.append(arquivo.name)
    con.execute("PRAGMA foreign_keys = ON")
    return aplicadas
