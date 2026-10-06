"""Gravação do cupom no banco: idempotência, itens e série de preço."""
from decimal import Decimal
from pathlib import Path

import pytest

from cupomzinho.banco import conectar
from cupomzinho.cupons import existe, fila_de_revisao, gravar, ler, recentes
from cupomzinho.nfce_pr import interpretar

FIXTURE = Path(__file__).parent / "fixtures" / "cupom_lar_2026-08-14.html"


@pytest.fixture
def con(tmp_path):
    con = conectar(tmp_path / "teste.db")
    yield con
    con.close()


@pytest.fixture
def cupom():
    return interpretar(FIXTURE.read_text(encoding="utf-8"))


def test_grava_e_relê(con, cupom):
    assert gravar(con, cupom, morador="Miguel") is True
    guardado = ler(con, cupom.chave)
    assert guardado["loja"] == "LAR COOPERATIVA AGROINDUSTRIAL"
    assert guardado["municipio"] == "MEDIANEIRA"
    assert guardado["morador"] == "Miguel"
    assert guardado["valor_pago"] == Decimal("106.74")
    assert guardado["desconto"] == Decimal("2.00")
    assert guardado["forma_pagamento"] == "Cartão de Crédito"
    assert guardado["completo"] is True
    assert len(guardado["itens"]) == 16


def test_reenviar_o_mesmo_cupom_nao_duplica(con, cupom):
    assert gravar(con, cupom, morador="Miguel") is True
    assert gravar(con, cupom, morador="Ana") is False        # 2ª vez: nada muda
    assert con.execute("SELECT COUNT(*) FROM cupom").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 16
    assert ler(con, cupom.chave)["morador"] == "Miguel"      # quem chegou primeiro


def test_quantidade_fracionaria_sobrevive_ao_banco(con, cupom):
    gravar(con, cupom, morador="Miguel")
    pepino = next(i for i in ler(con, cupom.chave)["itens"] if i["descricao"].startswith("PEPINO"))
    assert pepino["quantidade"] == Decimal("0.256")
    assert pepino["valor_unitario"] == Decimal("9.49")


def test_soma_dos_itens_no_banco_fecha_com_o_pago(con, cupom):
    """A aritmética tem de sobreviver à ida e volta em centavos."""
    gravar(con, cupom, morador="Miguel")
    somado = con.execute("SELECT SUM(total_centavos) FROM item WHERE chave = ?",
                         (cupom.chave,)).fetchone()[0]
    guardado = ler(con, cupom.chave)
    assert Decimal(somado) / 100 - guardado["desconto"] == guardado["valor_pago"]


def test_linhas_repetidas_viram_uma_observacao_de_preco(con, cupom):
    """O atum foi comprado duas vezes: dois itens, mas um preço observado.

    Contar duas vezes o mesmo preço na mesma loja e no mesmo instante inflaria
    o histórico sem acrescentar informação.
    """
    gravar(con, cupom, morador="Miguel")
    atum = con.execute(
        "SELECT produto_id FROM item WHERE descricao LIKE 'ATUM%' ORDER BY seq").fetchall()
    assert len(atum) == 2 and atum[0]["produto_id"] == atum[1]["produto_id"]
    precos = con.execute("SELECT COUNT(*) FROM preco WHERE produto_id = ?",
                         (atum[0]["produto_id"],)).fetchone()[0]
    assert precos == 1


def test_produto_e_apelido_de_loja_sao_criados(con, cupom):
    gravar(con, cupom, morador="Miguel")
    # 16 linhas, mas duas se repetem — o atum (itens 1 e 2, vizinhos) e o
    # iogurte de baunilha (itens 5 e 8, separados). Sobram 14 produtos.
    assert con.execute("SELECT COUNT(*) FROM produto").fetchone()[0] == 14
    assert con.execute("SELECT COUNT(*) FROM apelido_loja").fetchone()[0] == 14
    apelido = con.execute(
        "SELECT produto_id FROM apelido_loja WHERE descricao_norm = ?",
        ("ATUM GOM COSTA RALADO NAT 170G",),
    ).fetchone()
    assert apelido is not None       # o "NAT." do cupom virou "NAT" normalizado


def test_produto_repetido_longe_no_cupom_e_o_mesmo(con, cupom):
    """Itens 5 e 8 são o mesmo iogurte, mas não são linhas vizinhas."""
    gravar(con, cupom, morador="Miguel")
    baunilha = con.execute(
        "SELECT seq, produto_id FROM item WHERE descricao LIKE 'IOG%BAUNILHA%' ORDER BY seq"
    ).fetchall()
    assert [linha["seq"] for linha in baunilha] == [5, 8]
    assert baunilha[0]["produto_id"] == baunilha[1]["produto_id"]


def test_cupom_incompleto_vai_para_a_fila(con):
    quebrado = interpretar("<html><body>indisponível</body></html>")
    quebrado.chave = "9" * 44                       # só para ter chave primária
    quebrado.estabelecimento.cnpj = "0" * 14
    quebrado.estabelecimento.razao = "DESCONHECIDA"
    gravar(con, quebrado, morador="Miguel")
    fila = fila_de_revisao(con)
    assert len(fila) == 1 and fila[0]["chave"] == "9" * 44
    assert fila[0]["avisos"]


def test_recentes_e_existe(con, cupom):
    assert existe(con, cupom.chave) is False
    gravar(con, cupom, morador="Miguel")
    assert existe(con, cupom.chave) is True
    lista = recentes(con)
    assert len(lista) == 1 and lista[0]["itens"] == 16 and lista[0]["morador"] == "Miguel"


def test_backup_e_poda(con, cupom, tmp_path):
    """VACUUM INTO gera um banco íntegro, e só se guarda os últimos 7."""
    import sqlite3

    from cupomzinho.tarefas import fazer_backup

    gravar(con, cupom, morador="Miguel")
    pasta = tmp_path / "backups"

    alvo = fazer_backup(con, pasta)
    assert alvo is not None and alvo.exists()
    assert fazer_backup(con, pasta) is None          # já tem o de hoje

    copia = sqlite3.connect(alvo)
    assert copia.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 16
    copia.close()

    for dia in range(1, 11):                          # dez cópias falsas, antigas
        (pasta / f"cupomzinho_2026-01-{dia:02d}.db").write_bytes(b"")
    fazer_backup(con, pasta, guardar=7)
    assert len(list(pasta.glob("cupomzinho_*.db"))) == 7


def test_relatorio_conta_quantidade_e_nao_linha_de_cupom(con, cupom):
    """Contar linha mente nos dois sentidos, e as duas mentiras estão no cupom real.

    O atum ocupa DUAS linhas de 1 unidade na MESMA nota: contar linhas diria
    "2 compras", quando foi uma ida ao mercado com duas latas. E um item de
    seis unidades numa linha só diria "1", que foi como R$ 50,34 de bebida
    apareceram como comprados "1x".
    """
    from cupomzinho.relatorios import produtos, resumo_do_mes

    gravar(con, cupom, morador="Miguel")
    atum = next(p for p in produtos(con) if p["descricao"].startswith("ATUM"))
    assert atum["quantidade"] == Decimal("2.000")   # duas latas
    assert atum["compras"] == 1                     # numa ida só
    assert atum["unidade"] == "PT"

    maiores = resumo_do_mes(con, "2026-08")["maiores"]
    molho = next(m for m in maiores if m["descricao"].startswith("MOLHO"))
    assert molho["quantidade"] == Decimal("2.000")  # linha única, 2 unidades
    assert molho["compras"] == 1


def test_quantidade_some_quando_as_unidades_divergem(con, cupom):
    """Somar 2 CX com 0,5 KG daria um número sem significado — melhor não somar."""
    from cupomzinho.relatorios import produtos

    gravar(con, cupom, morador="Miguel")
    # o atum tem DUAS linhas: dá para deixar uma em PT e outra em KG
    con.execute("UPDATE item SET unidade = 'KG' WHERE seq = 2")
    con.commit()
    atum = next(p for p in produtos(con) if p["descricao"].startswith("ATUM"))
    assert atum["quantidade"] is None and atum["unidade"] is None
    assert atum["compras"] == 1        # o número de compras continua honesto


def test_gravar_completa_cupom_incompleto(con, cupom):
    """Cupom que entrou pela metade pode ser completado por reenvio ou reconsulta."""
    incompleto = interpretar("<html><body>erro</body></html>")
    incompleto.chave = cupom.chave
    incompleto.estabelecimento = cupom.estabelecimento
    assert gravar(con, incompleto, morador="Miguel") is True
    assert ler(con, cupom.chave)["completo"] is False
    assert len(fila_de_revisao(con)) == 1

    # Reenvio com o cupom completo real atualiza o registro
    assert gravar(con, cupom, morador="Miguel") is True
    salvo = ler(con, cupom.chave)
    assert salvo["completo"] is True
    assert len(salvo["itens"]) == 16
    assert len(fila_de_revisao(con)) == 0


def test_sped_completo_grava_e_enriquece_gtin_no_banco(con):
    """Consulta completa do SPED grava itens e enriquece GTIN/NCM em produto."""
    fixture_sped = Path(__file__).parent / "fixtures" / "cupom_sped_completo.html"
    cupom_sped = interpretar(fixture_sped.read_text(encoding="utf-8"))
    assert gravar(con, cupom_sped, morador="Miguel") is True

    salvo = ler(con, cupom_sped.chave)
    assert salvo["completo"] is True
    assert len(salvo["itens"]) == 3

    cafe = con.execute(
        "SELECT gtin, ncm, is_granel FROM produto WHERE descricao_canonica LIKE 'CAFE PILAO%'"
    ).fetchone()
    assert cafe["gtin"] == "7896005800119"
    assert cafe["ncm"] == "09012100"
    assert cafe["is_granel"] == 0

    maca = con.execute(
        "SELECT gtin, ncm, is_granel FROM produto WHERE descricao_canonica LIKE 'MACA FUJI%'"
    ).fetchone()
    assert maca["gtin"] is None
    assert maca["ncm"] == "08081000"
    assert maca["is_granel"] == 1

