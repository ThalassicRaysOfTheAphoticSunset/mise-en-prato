"""A lista de compras e a comparação entre mercados.

O teste central é o da cobertura: uma loja que só tem metade da lista não pode
ganhar por ser mais barata na metade que tem.

Vários testes usam `dias=1` de propósito. A fixture traz o cupom do LAR de
14/08, e os preços que a casa PAGOU entram na comparação junto com os
consultados — que é o comportamento certo, e o último teste do arquivo é sobre
ele. Nos cenários controlados, a janela curta deixa só os preços que o teste
inseriu.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from cupomzinho.banco import conectar
from cupomzinho.cupons import gravar
from cupomzinho.listas import comparar, criar, itens, por_produto, sugerir, tirar
from cupomzinho.nfce_pr import interpretar

FIXTURE = Path(__file__).parent / "fixtures" / "cupom_lar_2026-08-14.html"


@pytest.fixture
def con(tmp_path):
    con = conectar(tmp_path / "teste.db")
    gravar(con, interpretar(FIXTURE.read_text(encoding="utf-8")), morador="Miguel")
    yield con
    con.close()


def _produto(con, prefixo):
    return con.execute("SELECT id FROM produto WHERE descricao_canonica LIKE ?",
                       (prefixo + "%",)).fetchone()[0]


def _preco(con, produto_id, loja, centavos, *, fonte="menor_preco", quando=None):
    from datetime import datetime
    con.execute(
        "INSERT INTO preco (produto_id, unitario_centavos, observado_em, fonte, loja, municipio,"
        " codigo_menor_preco) VALUES (?,?,?,?,?,'MEDIANEIRA',?)",
        (produto_id, centavos,
         quando or datetime.now().isoformat(sep=" ", timespec="seconds"),
         fonte, loja, loja))
    con.commit()


def test_criar_e_montar_lista(con):
    lista = criar(con, "semana", "Miguel")
    atum = _produto(con, "ATUM")
    por_produto(con, lista, atum, Decimal("2"))
    guardados = itens(con, lista)
    assert len(guardados) == 1
    assert guardados[0]["quantidade"] == Decimal("2.000")

    por_produto(con, lista, atum, Decimal("3"))     # de novo: atualiza, não duplica
    assert itens(con, lista)[0]["quantidade"] == Decimal("3.000")

    tirar(con, lista, atum)
    assert itens(con, lista) == []


def test_sugestao_vem_do_que_a_casa_compra(con):
    sugeridos = sugerir(con)
    assert sugeridos, "a casa já tem cupom: tem de haver sugestão"
    assert all(s["compras"] >= 1 for s in sugeridos)
    assert sugeridos[0]["ultimo"] is not None


def test_loja_mais_barata_com_a_lista_inteira_ganha(con):
    lista = criar(con, "semana", "Miguel")
    atum, leite = _produto(con, "ATUM"), _produto(con, "LEITE FRIMESA INT")
    por_produto(con, lista, atum, Decimal("1"))
    por_produto(con, lista, leite, Decimal("2"))

    _preco(con, atum, "MUFFATO", 849)
    _preco(con, leite, "MUFFATO", 599)
    _preco(con, atum, "LAR", 1097)
    _preco(con, leite, "LAR", 549)

    r = comparar(con, lista, dias=1)
    assert r["itens"] == 2
    campeao = r["lojas"][0]
    assert campeao["loja"] == "MUFFATO"
    assert campeao["total"] == Decimal("20.47")      # 8,49 + 2 x 5,99
    assert campeao["completa"] and campeao["cobertura"] == 2


def test_loja_barata_pela_metade_nao_ganha_em_silencio(con):
    """O teste que justifica a existência da coluna cobertura."""
    lista = criar(con, "semana", "Miguel")
    atum, leite = _produto(con, "ATUM"), _produto(con, "LEITE FRIMESA INT")
    por_produto(con, lista, atum)
    por_produto(con, lista, leite)

    _preco(con, atum, "BARATINHO", 500)              # só tem o atum, e baratíssimo
    _preco(con, atum, "COMPLETO", 1000)
    _preco(con, leite, "COMPLETO", 600)

    r = comparar(con, lista, dias=1)
    assert r["lojas"][0]["loja"] == "COMPLETO", "a loja completa vem primeiro"
    assert r["lojas"][0]["total"] == Decimal("16.00")

    incompleta = r["lojas"][1]
    assert incompleta["loja"] == "BARATINHO"
    assert incompleta["total"] < r["lojas"][0]["total"]   # é mais barata, e mesmo assim perde
    assert incompleta["cobertura"] == 1 and incompleta["de"] == 2
    assert incompleta["falta"] == ["LEITE FRIMESA INT 1LT"]


def test_quantidade_multiplica_o_subtotal(con):
    lista = criar(con, "semana", "Miguel")
    leite = _produto(con, "LEITE FRIMESA INT")
    por_produto(con, lista, leite, Decimal("6"))
    _preco(con, leite, "LAR", 549)
    assert comparar(con, lista, dias=1)["lojas"][0]["total"] == Decimal("32.94")  # 6 x 5,49


def test_item_sem_preco_nenhum_e_declarado(con):
    lista = criar(con, "semana", "Miguel")
    por_produto(con, lista, _produto(con, "REPOLHO"))
    r = comparar(con, lista, dias=1)
    assert r["lojas"] == []
    assert r["sem_preco"] == ["REPOLHO ROXO KG"]


def test_preco_velho_fica_de_fora_da_janela(con):
    lista = criar(con, "semana", "Miguel")
    atum = _produto(con, "ATUM")
    por_produto(con, lista, atum)
    _preco(con, atum, "ANTIGA", 100, quando="2020-01-01 10:00:00")
    r = comparar(con, lista, dias=1)
    assert r["lojas"] == [] and r["sem_preco"] == ["ATUM GOM COSTA RALADO NAT 170G"]


def test_so_o_preco_mais_recente_de_cada_loja_conta(con):
    lista = criar(con, "semana", "Miguel")
    atum = _produto(con, "ATUM")
    por_produto(con, lista, atum)
    from datetime import datetime, timedelta
    ontem = (datetime.now() - timedelta(days=1)).isoformat(sep=" ", timespec="seconds")
    _preco(con, atum, "LAR", 2000, quando=ontem)
    _preco(con, atum, "LAR", 1097)
    lojas = comparar(con, lista, dias=1)["lojas"]
    assert len(lojas) == 1 and lojas[0]["total"] == Decimal("10.97")


def test_o_cupom_da_casa_tambem_serve_de_preco(con):
    """Sem consultar nada: o que a casa pagou já é comparação."""
    lista = criar(con, "semana", "Miguel")
    por_produto(con, lista, _produto(con, "ATUM"))
    # o preço do cupom foi gravado com a data de emissão, de 14/08
    r = comparar(con, lista, dias=3650)
    assert r["lojas"] and r["lojas"][0]["fontes"] == ["cupom"]
    assert r["lojas"][0]["total"] == Decimal("10.97")


def test_preco_pago_pela_casa_e_consultado_convivem(con):
    """As duas fontes entram na mesma comparação, e cada loja diz de onde veio.

    O cupom do LAR é de 14/08 e continua valendo dentro da janela; o preço do
    Muffato veio de consulta. A loja campeã aqui muda conforme o dado, e é
    exatamente essa a graça.
    """
    lista = criar(con, "semana", "Miguel")
    atum = _produto(con, "ATUM")
    por_produto(con, lista, atum)
    _preco(con, atum, "IRMAOS MUFFATO S.A", 849)

    r = comparar(con, lista, dias=365)
    por_loja = {loja["loja"]: loja for loja in r["lojas"]}
    assert por_loja["IRMAOS MUFFATO S.A"]["fontes"] == ["menor_preco"]
    assert por_loja["LAR COOPERATIVA AGROINDUSTRIAL"]["fontes"] == ["cupom"]
    # e o mais barato ganha: 8,49 do Muffato contra os 10,97 que a casa pagou
    assert r["lojas"][0]["loja"] == "IRMAOS MUFFATO S.A"


def test_filiais_da_mesma_rede_saem_separadas_e_com_distancia(con):
    """A API dá um código por filial, e o nome se repete.

    Três linhas "COOPERATIVA AGROINDUSTRIAL LAR" sem distância são
    indistinguíveis na tela — foi o que apareceu na primeira pesquisa real.
    """
    from datetime import datetime
    lista = criar(con, "semana", "Miguel")
    atum = _produto(con, "ATUM")
    por_produto(con, lista, atum)
    agora = datetime.now().isoformat(sep=" ", timespec="seconds")
    for codigo, distancia, centavos in (("filial-a", 0.5, 1097), ("filial-b", 3.3, 1050)):
        con.execute(
            "INSERT INTO preco (produto_id, unitario_centavos, observado_em, fonte, loja,"
            " municipio, codigo_menor_preco, distancia_km) VALUES (?,?,?,?,?,?,?,?)",
            (atum, centavos, agora, "menor_preco", "COOPERATIVA AGROINDUSTRIAL LAR",
             "MEDIANEIRA", codigo, distancia))
    con.commit()

    lojas = comparar(con, lista, dias=1)["lojas"]
    assert len(lojas) == 2, "duas filiais, duas linhas"
    assert {loja["distancia_km"] for loja in lojas} == {0.5, 3.3}
    assert lojas[0]["total"] == Decimal("10.50")     # a mais barata na frente


def test_janela_larga_nao_descarta_o_que_a_api_deu_por_bom(con):
    """A API devolve preço de duas semanas atrás mesmo pedindo data=7.

    Medido em 19/08/2026. Filtrar de novo em 7 dias do nosso lado fazia loja
    com preço válido sumir da comparação como se não vendesse o item.
    """
    from datetime import datetime, timedelta
    lista = criar(con, "semana", "Miguel")
    atum = _produto(con, "ATUM")
    por_produto(con, lista, atum)
    ha_duas_semanas = (datetime.now() - timedelta(days=14)).isoformat(sep=" ", timespec="seconds")
    _preco(con, atum, "MERCADO DISTANTE NO TEMPO", 800, quando=ha_duas_semanas)

    assert not any(loja["loja"] == "MERCADO DISTANTE NO TEMPO"
                   for loja in comparar(con, lista, dias=7)["lojas"])
    achado = next(loja for loja in comparar(con, lista)["lojas"]
                  if loja["loja"] == "MERCADO DISTANTE NO TEMPO")
    assert achado["mais_antigo"] == ha_duas_semanas[:10]
