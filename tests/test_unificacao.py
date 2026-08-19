"""Unificação de produtos: o que ela junta, e sobretudo o que ela NÃO junta.

O risco aqui é assimétrico. Deixar de sugerir um par custa um clique manual;
juntar dois produtos diferentes suja o histórico e é trabalhoso de desfazer.
Por isso metade destes testes é sobre recusar.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from cupomzinho.banco import conectar
from cupomzinho.cupons import gravar
from cupomzinho.nfce_pr import interpretar
from cupomzinho.unificacao import candidatos, parecidos, recusar, unificar

FIXTURE = Path(__file__).parent / "fixtures" / "cupom_lar_2026-08-14.html"


@pytest.fixture
def con(tmp_path):
    con = conectar(tmp_path / "teste.db")
    gravar(con, interpretar(FIXTURE.read_text(encoding="utf-8")), morador="Miguel")
    yield con
    con.close()


# ── o que parece o mesmo produto ─────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [
    # o caso real: o Muffato imprimiu os dois na MESMA nota
    ("ENERG MONST 473ML", "ENERG MONSTER 473ML"),
    ("LEITE FRIMESA INT 1LT", "LEITE FRIMESA INTEGRAL 1LT"),
    ("BISC PRINCESA 400G", "BISCOITO PRINCESA 400G"),
])
def test_abreviacao_e_candidata(a, b):
    assert parecidos(a, b) is not None


@pytest.mark.parametrize("a,b,porque", [
    ("ATUM GOM COSTA RALADO NAT 170G", "ATUM GOM COSTA PEDACOS NAT 170G",
     "ralado e pedaços são produtos diferentes, não abreviação"),
    ("IOG FRIMESA PROT BAUNILHA 170G", "IOG FRIMESA PROT MGO 170G",
     "sabores diferentes"),
    ("CHOC TRENTO SPEC AVELA LTE 26G", "CHOC TRENTO SPEC AVELA BCO 26G",
     "leite e branco: mesmo formato, produtos diferentes"),
    ("LEITE FRIMESA INT 1LT", "LEITE FRIMESA INT 2LT",
     "tamanhos diferentes nunca são o mesmo produto"),
    ("LEITE FRIMESA INT 1LT", "LEITE FRIMESA INTEGRAL LONGA VIDA 1LT",
     "número de fichas diferente: fora do alcance da regra estreita"),
    ("REF COCA COLA 2L", "REF PEPSI COLA 2L",
     "marcas diferentes"),
])
def test_o_que_a_regra_precisa_recusar(a, b, porque):
    assert parecidos(a, b) is None, porque


def test_duas_letras_nao_bastam_para_abreviacao():
    """"CO" prefixando "COCA" é fraco demais para virar sugestão."""
    assert parecidos("REF CO 2L", "REF COCA 2L") is None


# ── candidatos no banco ──────────────────────────────────────────────────────
def test_candidato_aparece_e_o_maior_historico_fica(con):
    con.execute("INSERT INTO produto (descricao_canonica) VALUES ('LEITE FRIMESA INTEGRAL 1LT')")
    con.commit()
    achados = candidatos(con)
    par = next(c for c in achados if "LEITE FRIMESA" in c["manter"]["descricao"])
    # o que já tem linha de cupom é o que fica; o recém-criado é absorvido
    assert par["manter"]["descricao"] == "LEITE FRIMESA INT 1LT"
    assert par["juntar"]["descricao"] == "LEITE FRIMESA INTEGRAL 1LT"


def test_codigo_de_barras_igual_vem_primeiro(con):
    gtin = "7891167011779"
    con.execute("UPDATE produto SET gtin = ? WHERE descricao_canonica LIKE 'ATUM%'", (gtin,))
    con.execute("INSERT INTO produto (descricao_canonica, gtin) VALUES ('OUTRA COISA', ?)",
                (gtin,))
    con.commit()
    primeiro = candidatos(con)[0]
    assert primeiro["motivo"] == "mesmo código de barras"


def test_par_recusado_nao_volta(con):
    con.execute("INSERT INTO produto (descricao_canonica) VALUES ('LEITE FRIMESA INTEGRAL 1LT')")
    con.commit()
    assert any("LEITE FRIMESA" in c["manter"]["descricao"] for c in candidatos(con))
    recusar(con, "LEITE FRIMESA INT 1LT", "LEITE FRIMESA INTEGRAL 1LT")
    assert not any("LEITE FRIMESA" in c["manter"]["descricao"] for c in candidatos(con))


# ── a fusão ──────────────────────────────────────────────────────────────────
def test_unificar_move_historico_e_apaga_o_absorvido(con):
    manter = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica LIKE 'ATUM%'").fetchone()[0]
    juntar = con.execute(
        "INSERT INTO produto (descricao_canonica, gtin, ncm)"
        " VALUES ('ATUM ABREV', '789', '16042010')").lastrowid
    con.execute("INSERT INTO item (chave, seq, descricao, quantidade_milesimos,"
                " unitario_centavos, total_centavos, produto_id)"
                " SELECT chave, 99, 'ATUM ABREV', 1000, 1097, 1097, ? FROM cupom LIMIT 1",
                (juntar,))
    con.commit()

    unificar(con, manter, juntar)

    assert con.execute("SELECT 1 FROM produto WHERE id = ?", (juntar,)).fetchone() is None
    linhas = con.execute("SELECT COUNT(*) FROM item WHERE produto_id = ?", (manter,)).fetchone()
    assert linhas[0] == 3
    # o que ficou não tinha código de barras nem ncm: herda de quem foi absorvido
    sobrevivente = con.execute("SELECT gtin, ncm FROM produto WHERE id = ?", (manter,)).fetchone()
    assert sobrevivente["gtin"] == "789" and sobrevivente["ncm"] == "16042010"


def test_unificar_nao_perde_dinheiro(con):
    """O gasto total do mês não pode mudar por causa de uma unificação."""
    from cupomzinho.relatorios import resumo_do_mes
    antes = resumo_do_mes(con, "2026-08")["total"]

    ids = [r[0] for r in con.execute(
        "SELECT id FROM produto WHERE descricao_canonica LIKE 'IOG%' ORDER BY id")]
    unificar(con, ids[0], ids[1])

    assert resumo_do_mes(con, "2026-08")["total"] == antes == Decimal("106.74")


def test_unificar_consigo_mesmo_e_erro(con):
    algum = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    with pytest.raises(ValueError, match="ele mesmo"):
        unificar(con, algum, algum)
