"""Unificação de produtos: o que ela junta, e sobretudo o que ela NÃO junta.

O risco aqui é assimétrico. Deixar de sugerir um par custa um clique manual;
juntar dois produtos diferentes suja o histórico e é trabalhoso de desfazer.
Por isso metade destes testes é sobre recusar.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from mise_en_prato.banco import conectar
from mise_en_prato.cupons import gravar
from mise_en_prato.nfce_pr import interpretar
from mise_en_prato.unificacao import (
    candidatos,
    parecidos,
    recusar,
    unificar,
    unificar_como_linha,
)

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
    from mise_en_prato.relatorios import resumo_do_mes
    antes = resumo_do_mes(con, "2026-08")["total"]

    ids = [r[0] for r in con.execute(
        "SELECT id FROM produto WHERE descricao_canonica LIKE 'IOG%' ORDER BY id")]
    unificar(con, ids[0], ids[1])

    assert resumo_do_mes(con, "2026-08")["total"] == antes == Decimal("106.74")


def test_unificar_consigo_mesmo_e_erro(con):
    algum = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    with pytest.raises(ValueError, match="ele mesmo"):
        unificar(con, algum, algum)


# ── unificar como linha multissabor ───────────────────────────────────────────
def test_candidatos_retorna_contexto_comparativo_completo(con):
    con.execute(
        """INSERT INTO produto (descricao_canonica, gtin, marca)
           VALUES ('LEITE FRIMESA INTEGRAL 1LT', '7891234567890', 'Frimesa')"""
    )
    con.commit()
    achados = candidatos(con)
    par = next(c for c in achados if "LEITE FRIMESA" in c["manter"]["descricao"])

    for lado in ("manter", "juntar"):
        assert "id" in par[lado]
        assert "descricao" in par[lado]
        assert "linhas" in par[lado]
        assert "gasto" in par[lado]
        assert "sabores" in par[lado]
        assert "menor_preco" in par[lado]
        assert "maior_preco" in par[lado]
        assert "ultima_data" in par[lado]
        assert "ultima_loja" in par[lado]


def test_unificar_como_linha_converte_produto_em_variante_e_migra_itens(con):
    mestre = con.execute(
        """INSERT INTO produto (descricao_canonica, gtin, marca)
           VALUES ('IOGURTE GREGO 90G', '7890001', 'Nestle')"""
    ).lastrowid
    outro = con.execute(
        """INSERT INTO produto (descricao_canonica, gtin, marca)
           VALUES ('IOGURTE GREGO MORANGO 90G', '7890002', 'Nestle')"""
    ).lastrowid

    # Cria compras para ambos
    sql_item = """
        INSERT INTO item (chave, seq, descricao, quantidade_milesimos,
                          unitario_centavos, total_centavos, produto_id, codigo_loja)
        SELECT chave, ?, ?, 1000, 450, 450, ?, ? FROM cupom LIMIT 1
    """
    con.execute(sql_item, (101, 'IOG GREGO TRAD', mestre, 'COD_TRAD'))
    con.execute(sql_item, (102, 'IOG GREGO MORANGO', outro, 'COD_MORANGO'))
    con.commit()

    res = unificar_como_linha(
        con, mestre, outro, sabor_mestre="Tradicional", sabor_variante="Morango"
    )
    assert res["mestre_id"] == mestre
    assert res["variante_id"] == outro
    assert res["sabor"] == "Morango"

    # O produto outro foi removido da tabela produto
    assert con.execute("SELECT 1 FROM produto WHERE id = ?", (outro,)).fetchone() is None

    # Foram criadas variantes sob o mestre
    vars_mestre = con.execute(
        "SELECT gtin, sabor FROM produto_variante WHERE produto_id = ? ORDER BY sabor",
        (mestre,),
    ).fetchall()
    sabores_map = {r["sabor"]: r["gtin"] for r in vars_mestre}
    assert "Morango" in sabores_map
    assert sabores_map["Morango"] == "7890002"
    assert "Tradicional" in sabores_map

    # Itens do produto absorvido agora pertencem ao mestre e têm sabor 'Morango'
    sql_outro = (
        "SELECT produto_id, sabor_adicional, gtin_variante "
        "FROM item WHERE codigo_loja = 'COD_MORANGO'"
    )
    item_outro = con.execute(sql_outro).fetchone()
    assert item_outro["produto_id"] == mestre
    assert item_outro["sabor_adicional"] == "Morango"
    assert item_outro["gtin_variante"] == "7890002"

    # Itens do mestre ganharam o sabor mestre
    item_mestre = con.execute(
        "SELECT produto_id, sabor_adicional FROM item WHERE codigo_loja = 'COD_TRAD'"
    ).fetchone()
    assert item_mestre["sabor_adicional"] == "Tradicional"


def test_unificar_como_linha_sem_gtin_preserva_integridade(con):
    mestre = con.execute(
        "INSERT INTO produto (descricao_canonica) VALUES ('BEBIDA LACTEA CHOCOLATE 200ML')"
    ).lastrowid
    outro = con.execute(
        "INSERT INTO produto (descricao_canonica) VALUES ('BEBIDA LACTEA MORANGO 200ML')"
    ).lastrowid

    unificar_como_linha(con, mestre, outro, sabor_variante="Morango")

    vars_mestre = con.execute(
        "SELECT gtin, sabor FROM produto_variante WHERE produto_id = ?",
        (mestre,),
    ).fetchall()
    assert any(r["sabor"] == "Morango" and r["gtin"].startswith("sabor:") for r in vars_mestre)


def test_unificar_como_linha_consigo_mesmo_e_erro(con):
    mestre = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    with pytest.raises(ValueError, match="ele mesmo"):
        unificar_como_linha(con, mestre, mestre)
