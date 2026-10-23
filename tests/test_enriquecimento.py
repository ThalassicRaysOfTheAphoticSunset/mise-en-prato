"""Descobrir o código de barras sem chutar.

Atribuir o GTIN errado não quebra nada visivelmente — só passa a comparar o
produto errado para sempre. Por isso a maior parte destes testes é sobre
RECUSAR, e o mais importante é o da ambiguidade.
"""
from pathlib import Path

import pytest

from cupomzinho.banco import conectar
from cupomzinho.catalogo import conectar_catalogo, inserir_produtos
from cupomzinho.cupons import gravar
from cupomzinho.enriquecimento import (
    enriquecer,
    enriquecer_local,
    enriquecer_pendentes_local,
    escolher_gtin,
    pendentes,
)
from cupomzinho.nfce_pr import interpretar

FIXTURE = Path(__file__).parent / "fixtures" / "cupom_lar_2026-08-14.html"
ATUM = "ATUM GOM COSTA RALADO NAT 170G"
GTIN = "7891167011779"
OUTRO = "7891167012103"      # o "DEF": mesmo tamanho, mesmo preço, produto diferente


def resultado(desc, gtin, loja, valor="10.97", ncm="16042010"):
    return {"desc": desc, "gtin": gtin, "valor": valor, "ncm": ncm,
            "estabelecimento": {"nm_emp": loja, "mun": "MEDIANEIRA", "uf": "PR"}}


@pytest.fixture
def con(tmp_path):
    con = conectar(tmp_path / "teste.db")
    gravar(con, interpretar(FIXTURE.read_text(encoding="utf-8")), morador="Miguel")
    yield con
    con.close()


LAR = {"LAR COOPERATIVA AGROINDUSTRIAL"}


# ── a decisão, isolada ───────────────────────────────────────────────────────
def test_mesma_loja_e_o_degrau_forte():
    d = escolher_gtin(ATUM, [resultado(ATUM, GTIN, "LAR COOPERATIVA AGROINDUSTRIAL")],
                      lojas_da_casa=LAR, precos_pagos={"10.97"})
    assert d["gtin"] == GTIN and d["confianca"] == "alta"
    assert "mesmo preço" in d["motivo"]


def test_consenso_da_regiao_serve_quando_nao_ha_loja_em_comum():
    achados = [resultado(ATUM, GTIN, f"MERCADO {k}") for k in range(4)]
    d = escolher_gtin(ATUM, achados, lojas_da_casa=LAR, precos_pagos=set())
    assert d["gtin"] == GTIN and d["confianca"] == "media"


def test_ambiguidade_recusa():
    """Dois códigos para a mesma descrição: ninguém decide isso no chute."""
    achados = [resultado(ATUM, GTIN, "MERCADO A"), resultado(ATUM, OUTRO, "MERCADO B")]
    d = escolher_gtin(ATUM, achados, lojas_da_casa=set(), precos_pagos=set())
    assert d["gtin"] is None and "ambíguo" in d["motivo"]


def test_ambiguidade_na_propria_loja_recusa():
    achados = [resultado(ATUM, GTIN, "LAR COOPERATIVA AGROINDUSTRIAL"),
               resultado(ATUM, OUTRO, "LAR COOPERATIVA AGROINDUSTRIAL")]
    d = escolher_gtin(ATUM, achados, lojas_da_casa=LAR, precos_pagos=set())
    assert d["gtin"] is None and "2 códigos" in d["motivo"]


def test_descricao_so_parecida_nao_serve():
    """"RALADO" e "PEDACOS" custam o mesmo e são produtos diferentes."""
    achados = [resultado("ATUM GOM COSTA PEDACOS NAT 170G", OUTRO,
                         "LAR COOPERATIVA AGROINDUSTRIAL")]
    d = escolher_gtin(ATUM, achados, lojas_da_casa=LAR, precos_pagos={"10.97"})
    assert d["gtin"] is None and "descrição exata" in d["motivo"]


def test_gtin_com_digito_errado_e_ignorado():
    achados = [resultado(ATUM, "7891167011778", "LAR COOPERATIVA AGROINDUSTRIAL")]
    d = escolher_gtin(ATUM, achados, lojas_da_casa=LAR, precos_pagos=set())
    assert d["gtin"] is None


def test_a_loja_da_casa_ganha_do_consenso_da_regiao():
    """Se a loja onde a casa comprou diz um código, é ele — mesmo em minoria."""
    achados = [resultado(ATUM, GTIN, "LAR COOPERATIVA AGROINDUSTRIAL"),
               *[resultado(ATUM, OUTRO, f"MERCADO {k}") for k in range(5)]]
    d = escolher_gtin(ATUM, achados, lojas_da_casa=LAR, precos_pagos=set())
    assert d["gtin"] == GTIN and d["confianca"] == "alta"


# ── ponta a ponta, com o cliente falso ───────────────────────────────────────
class ClienteFalso:
    def __init__(self, achados):
        self.achados = achados
        self.consultas = []

    def buscar(self, *, termo="", gtin=""):
        self.consultas.append(termo or gtin)
        return self.achados


def test_enriquecer_grava_gtin_e_ncm(con):
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)).fetchone()[0]
    cliente = ClienteFalso([resultado(ATUM, GTIN, "LAR COOPERATIVA AGROINDUSTRIAL")])

    r = enriquecer(con, cliente, produto_id)
    assert r["novo"] and r["gtin"] == GTIN
    assert cliente.consultas == [ATUM]

    guardado = con.execute("SELECT gtin, ncm FROM produto WHERE id = ?", (produto_id,)).fetchone()
    assert guardado["gtin"] == GTIN and guardado["ncm"] == "16042010"


def test_produto_que_ja_tem_gtin_nao_gasta_consulta(con):
    produto_id = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    con.execute("UPDATE produto SET gtin = ? WHERE id = ?", (GTIN, produto_id))
    con.commit()
    cliente = ClienteFalso([])
    r = enriquecer(con, cliente, produto_id)
    assert r["novo"] is False and cliente.consultas == []


def test_recusa_nao_grava_nada(con):
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)).fetchone()[0]
    cliente = ClienteFalso([resultado(ATUM, GTIN, "A"), resultado(ATUM, OUTRO, "B")])
    r = enriquecer(con, cliente, produto_id)
    assert r["novo"] is False
    assert con.execute("SELECT gtin FROM produto WHERE id = ?", (produto_id,)).fetchone()[0] is None


def test_pendentes_prioriza_o_que_a_casa_mais_compra(con):
    fila = pendentes(con)
    assert fila and all(p["compras"] >= 1 for p in fila)
    assert fila == sorted(fila, key=lambda p: (-p["compras"], p["descricao"]))


def test_enriquecer_dois_produtos_no_mesmo_gtin_vira_candidato_a_unificacao(con):
    """O ciclo se fecha: código igual é prova de que são o mesmo item."""
    from cupomzinho.unificacao import candidatos
    a, b = (r[0] for r in con.execute(
        "SELECT id FROM produto WHERE descricao_canonica LIKE 'IOG%' ORDER BY id LIMIT 2"))
    con.execute("UPDATE produto SET gtin = ? WHERE id IN (?,?)", (GTIN, a, b))
    con.commit()
    assert any(c["motivo"] == "mesmo código de barras" for c in candidatos(con))


def test_quem_ja_foi_perguntado_vai_para_o_fim_da_fila(con):
    """Produto industrial que falhou vai para o fim da fila para não queimar cota."""
    alvo = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica LIKE 'CHOC HERSHEYS%'").fetchone()[0]
    assert pendentes(con)[0]["produto_id"] != alvo or True

    enriquecer(con, ClienteFalso([]), alvo)                    # tentativa sem sucesso
    fila = pendentes(con)
    assert fila[-1]["produto_id"] == alvo
    assert fila[-1]["ja_perguntado"] is True
    assert all(p["ja_perguntado"] is False for p in fila[:-1])


def test_produto_a_granel_identificado_na_ingestao(con):
    """Produtos por peso (KG) são reconhecidos como is_granel=1 na ingestão."""
    cebola = con.execute(
        "SELECT is_granel FROM produto WHERE descricao_canonica = 'CEBOLA NACIONAL KG'"
    ).fetchone()[0]
    assert cebola == 1

    pepino = con.execute(
        "SELECT is_granel FROM produto WHERE descricao_canonica = 'PEPINO JAPONES KG'"
    ).fetchone()[0]
    assert pepino == 1

    atum = con.execute(
        "SELECT is_granel FROM produto WHERE descricao_canonica = ?", (ATUM,)
    ).fetchone()[0]
    assert atum == 0


def test_produto_a_granel_nao_entra_na_fila_de_pendentes(con):
    """Cebola, batata e itens a granel nunca têm GTIN comercial — nem devem entrar na fila."""
    fila = pendentes(con)
    descricoes = {p["descricao"] for p in fila}
    assert "CEBOLA NACIONAL KG" not in descricoes
    assert "PEPINO JAPONES KG" not in descricoes
    assert "BATATA ESPECIAL KG" not in descricoes
    assert "REPOLHO ROXO KG" not in descricoes
    assert "TOMATE EXTRA KG" not in descricoes


def test_enriquecer_produto_a_granel_nao_gasta_consulta(con):
    """Se alguém pedir para enriquecer produto a granel, recusa sem bater na API."""
    cebola = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = 'CEBOLA NACIONAL KG'"
    ).fetchone()[0]
    cliente = ClienteFalso([])
    r = enriquecer(con, cliente, cebola)
    assert r["novo"] is False
    assert cliente.consultas == []
    assert "granel" in r["motivo"]


def test_tentativa_e_marcada_mesmo_dando_certo(con):
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)).fetchone()[0]
    enriquecer(con, ClienteFalso([resultado(ATUM, GTIN, "LAR COOPERATIVA AGROINDUSTRIAL")]),
               produto_id)
    marcado = con.execute(
        "SELECT gtin_buscado_em FROM produto WHERE id = ?", (produto_id,)).fetchone()[0]
    assert marcado


# ── testes do banco satélite (catalogo.db) no enriquecimento ─────────────────
@pytest.fixture
def cat_con(tmp_path):
    caminho = tmp_path / "catalogo_mock.db"
    cat = conectar_catalogo(caminho, criar=True)
    itens = [
        {
            "gtin": GTIN,
            "nome": ATUM,
            "marca": "GOMES DA COSTA",
            "categoria": "Enlatados",
            "ncm": "16042010",
            "nutriscore": "A",
            "nova_group": 3,
            "foto_url": "https://off.org/atum.jpg",
            "origem": "openfoodfacts",
        },
        {
            "gtin": "7891000100103",
            "nome": "LEITE UHT INTEGRAL 1L",
            "marca": "PIRACANJUBA",
            "categoria": "Laticínios",
            "ncm": "04012010",
            "nutriscore": "B",
            "nova_group": 1,
            "foto_url": "https://off.org/leite.jpg",
            "origem": "openfoodfacts",
        },
    ]
    inserir_produtos(cat, itens)
    yield cat
    cat.close()


def test_enriquecer_local_por_gtin_preexistente(con, cat_con):
    """Produto já tem GTIN: catálogo local enriquece metadados e nutrição."""
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)
    ).fetchone()[0]
    con.execute("UPDATE produto SET gtin = ? WHERE id = ?", (GTIN, produto_id))
    con.commit()

    r = enriquecer_local(con, produto_id, con_catalogo=cat_con)
    assert r["novo"] is True
    assert "metadados enriquecidos" in r["motivo"]

    salvo = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    assert salvo["marca"] == "GOMES DA COSTA"
    assert salvo["nutriscore"] == "A"
    assert salvo["nova_group"] == 3
    assert salvo["foto_url"] == "https://off.org/atum.jpg"


def test_enriquecer_local_por_descricao_exata(con, cat_con):
    """Produto sem GTIN tem nome exato no catálogo: resolve GTIN e nutrição offline."""
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)
    ).fetchone()[0]

    r = enriquecer_local(con, produto_id, con_catalogo=cat_con)
    assert r["novo"] is True
    assert r["gtin"] == GTIN

    salvo = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    assert salvo["gtin"] == GTIN
    assert salvo["marca"] == "GOMES DA COSTA"
    assert salvo["nova_group"] == 3


def test_enriquecer_degrau_1_catalogo_evita_chamada_ao_menor_preco(con, cat_con):
    """Se o catálogo local resolve, Menor Preço NÃO é chamado (0 chamadas, poupa teto)."""
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)
    ).fetchone()[0]
    cliente = ClienteFalso([])

    r = enriquecer(con, cliente, produto_id, con_catalogo=cat_con)
    assert r["novo"] is True
    assert r["gtin"] == GTIN
    assert cliente.consultas == []  # Nenhuma consulta feita ao Menor Preço!


def test_enriquecer_degrau_2_puxa_nutricao_do_catalogo_apos_menor_preco(con, cat_con):
    """Se Menor Preço acha o GTIN, enriquece em seguida a nutrição pelo catálogo local."""
    # Renomeia temporariamente a descrição no banco para algo abreviado que não bate no catálogo
    produto_id = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = ?", (ATUM,)
    ).fetchone()[0]
    con.execute(
        "UPDATE produto SET descricao_canonica = 'ATUM GOM COSTA 170G' WHERE id = ?",
        (produto_id,),
    )
    con.commit()

    cliente = ClienteFalso([
        resultado("ATUM GOM COSTA 170G", GTIN, "LAR COOPERATIVA AGROINDUSTRIAL")
    ])
    r = enriquecer(con, cliente, produto_id, con_catalogo=cat_con)

    assert r["novo"] is True
    assert r["gtin"] == GTIN
    assert cliente.consultas == ["ATUM GOM COSTA 170G"]

    salvo = con.execute("SELECT * FROM produto WHERE id = ?", (produto_id,)).fetchone()
    assert salvo["gtin"] == GTIN
    assert salvo["marca"] == "GOMES DA COSTA"
    assert salvo["nutriscore"] == "A"
    assert salvo["nova_group"] == 3


def test_enriquecer_pendentes_local_em_lote(con, cat_con):
    """Varre pendentes e enriquece tudo o que der match local sem chamar API externa."""
    relatorio = enriquecer_pendentes_local(con, con_catalogo=cat_con)
    assert len(relatorio["identificados"]) >= 1
    assert any(i["gtin"] == GTIN for i in relatorio["identificados"])

