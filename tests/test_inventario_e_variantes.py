"""Testes do inventário doméstico de itens, sabores, memória de loja e desmembramento."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from mise_en_prato.banco import conectar
from mise_en_prato.catalogo import (
    consultar_openfoodfacts_api,
    listar_variantes,
    parse_openfoodfacts_dict,
    salvar_variantes_do_produto,
)
from mise_en_prato.cupons import gravar, ler
from mise_en_prato.enriquecimento import buscar_metadados_ean
from mise_en_prato.main import app
from mise_en_prato.nfce_pr import Cupom, Estabelecimento, Item
from mise_en_prato.unificacao import unificar


@pytest.fixture
def con(tmp_path):
    caminho = tmp_path / "teste_inventario.db"
    c = conectar(caminho)
    yield c
    c.close()


def test_migracao_010_estrutura(con):
    """Garante que a migração 010 adicionou as colunas em item e codigo_loja_produto."""
    info_item = {r["name"] for r in con.execute("PRAGMA table_info(item)").fetchall()}
    assert "gtin_variante" in info_item
    assert "sabor_adicional" in info_item

    info_clp = {r["name"] for r in con.execute("PRAGMA table_info(codigo_loja_produto)").fetchall()}
    esperados = {"cnpj", "codigo_loja", "produto_id", "gtin", "sabor", "atualizado_em"}
    assert esperados.issubset(info_clp)

    indices = {r["name"] for r in con.execute("PRAGMA index_list(codigo_loja_produto)").fetchall()}
    assert "idx_clp_produto" in indices


def test_parse_openfoodfacts_extrai_sabor_e_dados():
    """Valida extração de sabor, marcas e metadados no parse do Open Food Facts."""
    dados_mock = {
        "code": "7896045102556",
        "product_name": "Bebida Láctea UHT Cappuccino Classic 3 Corações 260ml",
        "brands": "3 Corações",
        "nutriscore_grade": "d",
        "nova_group": 4,
        "image_url": "https://images.openfoodfacts.org/images/products/classic.jpg",
        "flavor": "Classic",
    }
    parsed = parse_openfoodfacts_dict(dados_mock)
    assert parsed is not None
    assert parsed["gtin"] == "7896045102556"
    assert parsed["nome"] == "Bebida Láctea UHT Cappuccino Classic 3 Corações 260ml"
    assert parsed["sabor"] == "Classic"
    assert parsed["marca"] == "3 Corações"
    assert parsed["nutriscore"] == "D"
    assert parsed["nova_group"] == 4
    assert parsed["foto_url"] == "https://images.openfoodfacts.org/images/products/classic.jpg"


def test_parse_openfoodfacts_extrai_sabor_de_nome_generico():
    """Valida que quando 'flavor' não existe, extrai sabor puro de generic_name_pt."""
    dados_mock = {
        "code": "7896045102556",
        "product_name": "Bebida Láctea 260ml",
        "generic_name_pt": "Bebida Láctea UHT sabor Morango",
        "brands": "Piracanjuba",
    }
    parsed = parse_openfoodfacts_dict(dados_mock)
    assert parsed is not None
    assert parsed["sabor"] == "Morango"


def test_consultar_openfoodfacts_api_mock():
    """Valida chamada mockada para a API v0 do Open Food Facts."""
    resposta_api = {
        "status": 1,
        "product": {
            "code": "7896045102563",
            "product_name": "Bebida Láctea Chocolatto 3 Corações 260ml",
            "brands": "3 Corações",
            "flavor": "Chocolatto",
            "nutriscore_grade": "d",
            "nova_group": 4,
        },
    }

    with patch("urllib.request.urlopen") as mock_url:
        import io
        import json

        mock_url.return_value.__enter__.return_value = io.BytesIO(
            json.dumps(resposta_api).encode("utf-8")
        )
        res = consultar_openfoodfacts_api("7896045102563")
        assert res is not None
        assert res["gtin"] == "7896045102563"
        assert res["sabor"] == "Chocolatto"
        assert res["nome"] == "Bebida Láctea Chocolatto 3 Corações 260ml"

        mock_url.return_value.__enter__.return_value = io.BytesIO(
            json.dumps(resposta_api).encode("utf-8")
        )
        res_ean = buscar_metadados_ean("7896045102563")
        assert res_ean is not None
        assert res_ean["sabor"] == "Chocolatto"


def test_salvar_variantes_preenche_nome_real_off(con):
    """Ao salvar variante, substitui descrição truncada pelo nome limpo do catálogo/OFF."""
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (1, 'BEB LA 3 CORAC 260ML')")
    con.commit()

    resposta_api = {
        "status": 1,
        "product": {
            "code": "7896045102556",
            "product_name": "Bebida Láctea Cappuccino Classic 3 Corações 260ml",
            "brands": "3 Corações",
            "flavor": "Classic",
        },
    }

    with patch("mise_en_prato.catalogo.consultar_openfoodfacts_api") as mock_off:
        mock_off.return_value = parse_openfoodfacts_dict(resposta_api["product"])
        salvar_variantes_do_produto(
            con,
            1,
            [{"gtin": "7896045102556", "nome": "BEB LA 3 CORAC 260ML"}],
        )

    vars = listar_variantes(con, 1)
    assert len(vars) == 1
    assert vars[0]["nome"] == "Bebida Láctea Cappuccino Classic 3 Corações 260ml"
    assert vars[0]["sabor"] == "Classic"


def test_aprendizado_e_auto_preenchimento_ao_importar_cupom(con):
    """Quando codigo_loja_produto possui mapeamento, novo cupom já importa item enriquecido."""
    cnpj_loja = "00000000000191"
    cod_interno = "314121"

    cur = con.execute(
        "INSERT INTO produto (descricao_canonica) VALUES ('IOGURTE GREGO FRUTAS VERMELHAS 450G')"
    )
    prod_grego_id = cur.lastrowid
    salvar_variantes_do_produto(
        con,
        prod_grego_id,
        [
            {
                "gtin": "7896691105017",
                "nome": "Iogurte Grego Frutas Vermelhas 450g",
                "sabor": "Frutas Vermelhas",
            }
        ],
    )

    con.execute(
        """INSERT INTO codigo_loja_produto
               (cnpj, codigo_loja, produto_id, gtin, sabor, atualizado_em)
           VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))""",
        (cnpj_loja, cod_interno, prod_grego_id, "7896691105017", "Frutas Vermelhas"),
    )
    con.commit()

    cupom = Cupom(
        chave="41261000000000000191650010000000011000000010",
        estabelecimento=Estabelecimento(cnpj=cnpj_loja, razao="Supermercado Teste"),
        total=Decimal("8.99"),
        desconto=Decimal("0.00"),
        valor_pago=Decimal("8.99"),
        itens=[
            Item(
                seq=1,
                descricao="IOG CAROLINA 450G",
                codigo_loja=cod_interno,
                unidade="UN",
                quantidade=Decimal("1"),
                valor_unitario=Decimal("8.99"),
                valor_total=Decimal("8.99"),
            )
        ],
    )

    sucesso = gravar(con, cupom, morador="miguel")
    assert sucesso is True

    item = con.execute("SELECT * FROM item WHERE chave = ?", (cupom.chave,)).fetchone()
    assert item["produto_id"] == prod_grego_id
    assert item["gtin_variante"] == "7896691105017"
    assert item["sabor_adicional"] == "Frutas Vermelhas"

    dados_cupom = ler(con, cupom.chave)
    assert dados_cupom is not None
    it = dados_cupom["itens"][0]
    assert it["gtin_variante"] == "7896691105017"
    assert it["sabor_adicional"] == "Frutas Vermelhas"
    assert len(it["variantes"]) >= 1


def test_api_associar_variante_item(tmp_path, monkeypatch):
    """Testa endpoint POST /api/item/{id}/variante com lembrar_loja e retroagir."""
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    from mise_en_prato.config import config
    config.cache_clear()

    db_path = tmp_path / "teste_api_item.db"
    con = conectar(db_path)

    cnpj = "12345678000199"
    con.execute("INSERT INTO estabelecimento (cnpj, razao) VALUES (?, 'Mercado X')", (cnpj,))
    con.execute(
        "INSERT INTO produto (id, descricao_canonica) VALUES (10, 'BEB LA 3 CORAC 260ML')"
    )

    con.execute(
        """INSERT INTO cupom (chave, cnpj, emitido_em, completo, criado_em)
           VALUES ('c1', ?, '2026-09-01', 1, '2026-09-01')""",
        (cnpj,),
    )
    con.execute(
        """INSERT INTO item (id, chave, seq, descricao, codigo_loja, unidade,
                             quantidade_milesimos, unitario_centavos, total_centavos, produto_id)
           VALUES (101, 'c1', 1, 'BEB LA 3 CORAC 260ML', 'PLU444', 'UN', 1000, 839, 839, 10)"""
    )

    con.execute(
        """INSERT INTO cupom (chave, cnpj, emitido_em, completo, criado_em)
           VALUES ('c2', ?, '2026-09-05', 1, '2026-09-05')""",
        (cnpj,),
    )
    con.execute(
        """INSERT INTO item (id, chave, seq, descricao, codigo_loja, unidade,
                             quantidade_milesimos, unitario_centavos, total_centavos, produto_id)
           VALUES (102, 'c2', 1, 'BEB LA 3 CORAC 260ML', 'PLU444', 'UN', 1000, 839, 839, 10)"""
    )
    con.commit()

    app.state.con = con
    client = TestClient(app)

    resp = client.post(
        "/api/item/101/variante",
        json={
            "gtin": "7896045102563",
            "sabor": "Chocolatto",
            "lembrar_loja": True,
            "retroagir": True,
        },
    )
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["ok"] is True
    assert dados["sabor"] == "Chocolatto"
    assert dados["gtin"] == "7896045102563"
    assert dados["retroagidos"] == 1

    it1 = con.execute("SELECT * FROM item WHERE id = 101").fetchone()
    assert it1["gtin_variante"] == "7896045102563"
    assert it1["sabor_adicional"] == "Chocolatto"

    it2 = con.execute("SELECT * FROM item WHERE id = 102").fetchone()
    assert it2["gtin_variante"] == "7896045102563"
    assert it2["sabor_adicional"] == "Chocolatto"

    mem = con.execute(
        "SELECT * FROM codigo_loja_produto WHERE cnpj = ? AND codigo_loja = 'PLU444'",
        (cnpj,),
    ).fetchone()
    assert mem is not None
    assert mem["produto_id"] == 10
    assert mem["gtin"] == "7896045102563"
    assert mem["sabor"] == "Chocolatto"


def test_api_desmembrar_produto(tmp_path, monkeypatch):
    """Testa endpoint POST /api/produto/{id}/desmembrar separando variante em produto autônomo."""
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    from mise_en_prato.config import config
    config.cache_clear()

    db_path = tmp_path / "teste_api_desmembrar.db"
    con = conectar(db_path)

    cnpj = "99999999000100"
    con.execute(
        "INSERT INTO estabelecimento (cnpj, razao) VALUES (?, 'Supermercado Central')", (cnpj,)
    )
    con.execute("""
        INSERT INTO produto (id, descricao_canonica, gtin, marca)
        VALUES (500, 'IOG CAROLINA 450G', '7896691105000', 'Carolina')
    """)

    salvar_variantes_do_produto(
        con,
        500,
        [
            {
                "gtin": "7896691105000",
                "nome": "Iogurte Carolina Tradicional 450g",
                "sabor": "Tradicional",
                "eh_padrao": True,
            },
            {
                "gtin": "7896691105017",
                "nome": "Iogurte Carolina Grego 450g",
                "sabor": "Grego",
                "eh_padrao": False,
            },
        ],
    )

    con.execute(
        """INSERT INTO cupom (chave, cnpj, emitido_em, completo, criado_em)
           VALUES ('chave-iog', ?, '2026-09-10', 1, '2026-09-10')""",
        (cnpj,),
    )
    con.execute(
        """INSERT INTO item (id, chave, seq, descricao, codigo_loja, unidade,
                             quantidade_milesimos, unitario_centavos, total_centavos,
                             produto_id, gtin_variante, sabor_adicional)
           VALUES (5001, 'chave-iog', 1, 'IOG CAROLINA 450G', 'PLU-GREGO', 'UN',
                   1000, 899, 899, 500, '7896691105017', 'Grego')"""
    )
    con.execute(
        """INSERT INTO codigo_loja_produto
               (cnpj, codigo_loja, produto_id, gtin, sabor, atualizado_em)
           VALUES (?, 'PLU-GREGO', 500, '7896691105017', 'Grego', datetime('now'))""",
        (cnpj,),
    )
    con.commit()

    app.state.con = con
    client = TestClient(app)

    resp = client.post(
        "/api/produto/500/desmembrar",
        json={
            "gtin": "7896691105017",
            "novo_nome": "Iogurte Carolina Grego Frutas Vermelhas 450g",
            "codigo_loja": "PLU-GREGO",
            "cnpj": cnpj,
        },
    )
    assert resp.status_code == 200
    res = resp.json()
    assert res["ok"] is True
    novo_id = res["novo_produto_id"]
    assert novo_id != 500

    novo_prod = con.execute("SELECT * FROM produto WHERE id = ?", (novo_id,)).fetchone()
    assert novo_prod["descricao_canonica"] == "Iogurte Carolina Grego Frutas Vermelhas 450g"
    assert novo_prod["gtin"] == "7896691105017"

    vars_novo = listar_variantes(con, novo_id)
    assert len(vars_novo) == 1
    assert vars_novo[0]["gtin"] == "7896691105017"

    vars_orig = listar_variantes(con, 500)
    assert len(vars_orig) == 1
    assert vars_orig[0]["gtin"] == "7896691105000"

    it = con.execute("SELECT * FROM item WHERE id = 5001").fetchone()
    assert it["produto_id"] == novo_id

    clp = con.execute(
        "SELECT * FROM codigo_loja_produto WHERE cnpj = ? AND codigo_loja = 'PLU-GREGO'",
        (cnpj,),
    ).fetchone()
    assert clp["produto_id"] == novo_id


def test_unificacao_preserva_codigo_loja_produto(con):
    """Regressão: ao unificar produtos, a memória de loja de codigo_loja_produto não é perdida."""
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (70, 'PROD A')")
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (80, 'PROD B')")

    con.execute("""
        INSERT INTO codigo_loja_produto (cnpj, codigo_loja, produto_id, gtin, sabor, atualizado_em)
        VALUES ('111', 'COD1', 80, '7891234567890', 'Morango', datetime('now'))
    """)
    con.commit()

    unificar(con, manter=70, juntar=80)

    clp = con.execute(
        "SELECT * FROM codigo_loja_produto WHERE cnpj = '111' AND codigo_loja = 'COD1'"
    ).fetchone()
    assert clp is not None
    assert clp["produto_id"] == 70


def test_api_associar_sabor_sem_gtin(tmp_path, monkeypatch):
    """Testa associação apenas de sabor (sem GTIN), evitando criação de GTIN fake."""
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    from mise_en_prato.config import config
    config.cache_clear()

    db_path = tmp_path / "teste_sabor_sem_gtin.db"
    con = conectar(db_path)

    cnpj = "12345678000199"
    con.execute("INSERT INTO estabelecimento (cnpj, razao) VALUES (?, 'Mercado Y')", (cnpj,))
    con.execute(
        "INSERT INTO produto (id, descricao_canonica) VALUES (20, 'IOGURTE LIQUIDO 170G')"
    )
    con.execute(
        """INSERT INTO cupom (chave, cnpj, emitido_em, completo, criado_em)
           VALUES ('c_sabor', ?, '2026-09-01', 1, '2026-09-01')""",
        (cnpj,),
    )
    con.execute(
        """INSERT INTO item (id, chave, seq, descricao, codigo_loja, unidade,
                             quantidade_milesimos, unitario_centavos, total_centavos, produto_id)
           VALUES (201, 'c_sabor', 1, 'IOGURTE LIQUIDO 170G', 'PLU-MOR', 'UN', 1000, 450, 450,
                   20)"""
    )
    con.commit()

    app.state.con = con
    client = TestClient(app)

    # 1. Requisição apenas com sabor (gtin ausente)
    resp = client.post(
        "/api/item/201/variante",
        json={"sabor": "Morango", "lembrar_loja": True, "retroagir": False},
    )
    assert resp.status_code == 200
    dados = resp.json()
    assert dados["ok"] is True
    assert dados["sabor"] == "Morango"
    assert dados["gtin"] is None

    it = con.execute("SELECT * FROM item WHERE id = 201").fetchone()
    assert it["sabor_adicional"] == "Morango"
    assert it["gtin_variante"] is None

    # Garante que não criou variante '0000000000000'
    fake_var = con.execute(
        "SELECT * FROM produto_variante WHERE gtin = '0000000000000'"
    ).fetchone()
    assert fake_var is None

    clp = con.execute(
        "SELECT * FROM codigo_loja_produto WHERE cnpj = ? AND codigo_loja = 'PLU-MOR'",
        (cnpj,),
    ).fetchone()
    assert clp is not None
    assert clp["produto_id"] == 20
    assert clp["sabor"] == "Morango"
    assert clp["gtin"] is None


def test_api_catalogo_ean_endpoint(tmp_path, monkeypatch):
    """Testa endpoint GET /api/catalogo/ean/{gtin}."""
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    from mise_en_prato.config import config
    config.cache_clear()

    db_path = tmp_path / "teste_api_catalogo.db"
    con = conectar(db_path)
    app.state.con = con
    client = TestClient(app)

    with patch("mise_en_prato.enriquecimento.buscar_metadados_ean") as mock_meta:
        mock_meta.return_value = {
            "gtin": "7896045102563",
            "nome": "Bebida Láctea Chocolatto 3 Corações 260ml",
            "sabor": "Chocolatto",
        }
        resp = client.get("/api/catalogo/ean/7896045102563")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sabor"] == "Chocolatto"
        assert data["gtin"] == "7896045102563"


def test_salvar_variantes_reseta_padrao_anterior(con):
    """Quando uma nova variante é marcada como padrão, a anterior deixa de ser padrão."""
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (30, 'BEBIDA LACTEA 260ML')")
    con.commit()

    salvar_variantes_do_produto(
        con,
        30,
        [{"gtin": "7896045102556", "nome": "Classic", "eh_padrao": True}],
    )
    v1 = con.execute(
        "SELECT eh_padrao FROM produto_variante WHERE gtin = '7896045102556'"
    ).fetchone()[0]
    assert v1 == 1

    # Adiciona segunda variante marcando como nova padrão
    salvar_variantes_do_produto(
        con,
        30,
        [{"gtin": "7896045102563", "nome": "Chocolatto", "eh_padrao": True}],
    )
    v1_depois = con.execute(
        "SELECT eh_padrao FROM produto_variante WHERE gtin = '7896045102556'"
    ).fetchone()[0]
    v2_depois = con.execute(
        "SELECT eh_padrao FROM produto_variante WHERE gtin = '7896045102563'"
    ).fetchone()[0]
    assert v1_depois == 0
    assert v2_depois == 1


def test_desmembrar_limpa_precos_pai_e_preenche_itens(tmp_path, monkeypatch):
    """Desmembrar deve remover preços obsoletos do pai e preencher sabor/EAN nos itens."""
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    from mise_en_prato.config import config
    config.cache_clear()

    db_path = tmp_path / "teste_desmembrar_precos.db"
    con = conectar(db_path)

    cnpj = "88888888000188"
    con.execute("INSERT INTO estabelecimento (cnpj, razao) VALUES (?, 'Super X')", (cnpj,))
    con.execute(
        """INSERT INTO produto (id, descricao_canonica, gtin)
           VALUES (600, 'IOG CAROLINA 450G', '7896691105000')"""
    )

    salvar_variantes_do_produto(
        con,
        600,
        [
            {
                "gtin": "7896691105000",
                "nome": "Carolina Tradicional",
                "sabor": "Tradicional",
                "eh_padrao": True,
            },
            {
                "gtin": "7896691105017",
                "nome": "Carolina Grego",
                "sabor": "Grego",
                "eh_padrao": False,
            },
        ],
    )

    con.execute(
        """INSERT INTO cupom (chave, cnpj, emitido_em, completo, criado_em)
           VALUES ('c_trad', ?, '2026-09-01', 1, '2026-09-01')""",
        (cnpj,),
    )
    con.execute(
        """INSERT INTO item (id, chave, seq, descricao, codigo_loja, unidade,
                             quantidade_milesimos, unitario_centavos, total_centavos, produto_id)
           VALUES (6001, 'c_trad', 1, 'IOG CAROLINA 450G', 'PLU-TRAD', 'UN', 1000, 789, 789,
                   600)"""
    )
    con.execute(
        """INSERT INTO preco (produto_id, cnpj, unitario_centavos, observado_em, fonte)
           VALUES (600, ?, 789, '2026-09-01', 'cupom')""",
        (cnpj,),
    )

    con.execute(
        """INSERT INTO cupom (chave, cnpj, emitido_em, completo, criado_em)
           VALUES ('c_grego', ?, '2026-09-05', 1, '2026-09-05')""",
        (cnpj,),
    )
    con.execute(
        """INSERT INTO item (id, chave, seq, descricao, codigo_loja, unidade,
                             quantidade_milesimos, unitario_centavos, total_centavos, produto_id)
           VALUES (6002, 'c_grego', 1, 'IOG CAROLINA 450G', 'PLU-GREGO', 'UN', 1000, 899, 899,
                   600)"""
    )
    con.execute(
        """INSERT INTO preco (produto_id, cnpj, unitario_centavos, observado_em, fonte)
           VALUES (600, ?, 899, '2026-09-05', 'cupom')""",
        (cnpj,),
    )
    con.commit()

    app.state.con = con
    client = TestClient(app)

    resp = client.post(
        "/api/produto/600/desmembrar",
        json={
            "gtin": "7896691105017",
            "novo_nome": "Iogurte Carolina Grego 450g",
            "codigo_loja": "PLU-GREGO",
            "cnpj": cnpj,
        },
    )
    assert resp.status_code == 200
    novo_id = resp.json()["novo_produto_id"]

    # 1. Preços do pai (600) NÃO devem mais conter 899, apenas 789
    precos_pai = [
        r["unitario_centavos"]
        for r in con.execute(
            "SELECT unitario_centavos FROM preco WHERE produto_id = 600"
        ).fetchall()
    ]
    assert 789 in precos_pai
    assert 899 not in precos_pai

    # 2. Preços do novo (novo_id) devem conter 899
    precos_novo = [
        r["unitario_centavos"]
        for r in con.execute(
            "SELECT unitario_centavos FROM preco WHERE produto_id = ?", (novo_id,)
        ).fetchall()
    ]
    assert 899 in precos_novo

    # 3. Item 6002: produto_id = novo_id, gtin_variante = 7896691105017 e sabor_adicional = Grego
    it_migrado = con.execute("SELECT * FROM item WHERE id = 6002").fetchone()
    assert it_migrado["produto_id"] == novo_id
    assert it_migrado["gtin_variante"] == "7896691105017"
    assert it_migrado["sabor_adicional"] == "Grego"

    # 4. codigo_loja_produto foi registrado com novo_id
    clp = con.execute(
        "SELECT * FROM codigo_loja_produto WHERE cnpj = ? AND codigo_loja = 'PLU-GREGO'",
        (cnpj,),
    ).fetchone()
    assert clp is not None
    assert clp["produto_id"] == novo_id
