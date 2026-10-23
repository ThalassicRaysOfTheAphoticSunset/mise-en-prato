"""Testes do banco satélite de catálogo offline e ingestão de dumps.

O catálogo satélite vive em dados/catalogo.db de forma isolada do banco
transacional principal dados/cupomzinho.db.
"""
import gzip
import json

import pytest

from cupomzinho.catalogo import (
    buscar_por_gtin,
    buscar_por_termo,
    conectar_catalogo,
    estatisticas_catalogo,
    ingestar_openfoodfacts_stream,
    inserir_produtos,
    parse_cmed_linha,
    parse_openfoodfacts_dict,
)


@pytest.fixture
def db_catalogo(tmp_path):
    caminho = tmp_path / "catalogo_teste.db"
    con = conectar_catalogo(caminho, criar=True)
    yield con
    con.close()


def test_conectar_catalogo_inexistente_sem_criar(tmp_path):
    caminho = tmp_path / "inexistente.db"
    con = conectar_catalogo(caminho, criar=False)
    assert con is None


def test_criar_e_conectar_catalogo(tmp_path):
    caminho = tmp_path / "novo.db"
    con = conectar_catalogo(caminho, criar=True)
    assert con is not None
    # Verifica que a tabela existe
    tabelas = [
        r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]
    assert "catalogo_produto" in tabelas
    con.close()


def test_inserir_e_buscar_por_gtin(db_catalogo):
    item = {
        "gtin": "7891000100103",
        "nome": "LEITE INTEGRAL 1L",
        "marca": "PIRACANJUBA",
        "categoria": "Laticínios",
        "ncm": "04012010",
        "nutriscore": "B",
        "nova_group": 1,
        "foto_url": "https://images.openfoodfacts.org/images/1.jpg",
        "origem": "openfoodfacts",
    }
    qtd = inserir_produtos(db_catalogo, [item])
    assert qtd == 1

    # Busca exata
    achado = buscar_por_gtin(db_catalogo, "7891000100103")
    assert achado is not None
    assert achado["nome"] == "LEITE INTEGRAL 1L"
    assert achado["marca"] == "PIRACANJUBA"
    assert achado["nutriscore"] == "B"
    assert achado["nova_group"] == 1

    # Busca com zero à esquerda (GTIN-14) deve normalizar e achar
    achado_14 = buscar_por_gtin(db_catalogo, "07891000100103")
    assert achado_14 is not None
    assert achado_14["gtin"] == "7891000100103"

    # GTIN inexistente
    assert buscar_por_gtin(db_catalogo, "7890000000000") is None


def test_buscar_por_termo(db_catalogo):
    produtos = [
        {"gtin": "7891000100103", "nome": "LEITE UHT INTEGRAL 1L", "origem": "openfoodfacts"},
        {"gtin": "7891000200202", "nome": "LEITE UHT DESNATADO 1L", "origem": "openfoodfacts"},
        {"gtin": "7892000300301", "nome": "BISCOITO RECHEADO CHOCOLATE", "origem": "openfoodfacts"},
    ]
    inserir_produtos(db_catalogo, produtos)

    leites = buscar_por_termo(db_catalogo, "leite integral")
    assert len(leites) == 1
    assert leites[0]["gtin"] == "7891000100103"


def test_parse_openfoodfacts_dict():
    # Produto brasileiro válido
    raw = {
        "code": "7891000100103",
        "product_name": "Leite UHT Integral",
        "brands": "Piracanjuba",
        "nutriscore_grade": "b",
        "nova_group": 1,
        "image_front_url": "https://example.com/foto.jpg",
        "countries_tags": ["en:brazil"],
    }
    parsed = parse_openfoodfacts_dict(raw)
    assert parsed is not None
    assert parsed["gtin"] == "7891000100103"
    assert parsed["nome"] == "Leite UHT Integral"
    assert parsed["marca"] == "Piracanjuba"
    assert parsed["nutriscore"] == "B"
    assert parsed["nova_group"] == 1
    assert parsed["foto_url"] == "https://example.com/foto.jpg"
    assert parsed["origem"] == "openfoodfacts"

    # Produto com código sem dígito verificador válido deve ser descartado
    invalido = {"code": "12345", "product_name": "Teste"}
    assert parse_openfoodfacts_dict(invalido) is None

    # Produto sem nome deve ser descartado
    sem_nome = {"code": "7891000100103", "product_name": ""}
    assert parse_openfoodfacts_dict(sem_nome) is None


def test_parse_cmed_linha():
    colunas = [
        "SUBSTÂNCIA", "LABORATÓRIO", "CÓDIGO GGFIS", "REGISTRO", "EAN 1",
        "PRODUTO", "APRESENTAÇÃO", "CLASSE TERAPÊUTICA"
    ]
    linha = [
        "PARACETAMOL", "MEDLEY FARMACÊUTICA LTDA", "123", "101810101",
        "7896422505512", "PARACETAMOL", "500 MG COM CT BL AL PLAS INC X 20", "ANALGÉSICOS"
    ]
    parsed = parse_cmed_linha(linha, colunas)
    assert parsed is not None
    assert parsed["gtin"] == "7896422505512"
    assert "PARACETAMOL" in parsed["nome"]
    assert "500 MG" in parsed["nome"]
    assert parsed["marca"] == "MEDLEY FARMACÊUTICA LTDA"
    assert parsed["categoria"] == "ANALGÉSICOS"
    assert parsed["origem"] == "cmed"


def test_ingestar_openfoodfacts_stream(tmp_path, db_catalogo):
    # Gera arquivo .jsonl.gz sintético
    arquivo_gz = tmp_path / "off_amostra.jsonl.gz"
    itens = [
        {
            "code": "7891000100103",
            "product_name": "Item A",
            "brands": "Marca A",
            "countries_tags": ["en:brazil"],
        },
        {
            "code": "7891000200202",
            "product_name": "Item B",
            "brands": "Marca B",
            "countries_tags": ["en:brazil"],
        },
        {"code": "0000000000000", "product_name": "Item Invalido"},
    ]
    with gzip.open(arquivo_gz, "wt", encoding="utf-8") as f:
        for it in itens:
            f.write(json.dumps(it) + "\n")

    processados, inseridos = ingestar_openfoodfacts_stream(db_catalogo, arquivo_gz)
    assert processados == 3
    assert inseridos == 2

    stats = estatisticas_catalogo(db_catalogo)
    assert stats["total"] == 2
    assert stats["por_origem"].get("openfoodfacts") == 2


def test_consultar_openfoodfacts_api_invalido():
    from cupomzinho.catalogo import consultar_openfoodfacts_api

    assert consultar_openfoodfacts_api("") is None
    assert consultar_openfoodfacts_api("123") is None
    assert consultar_openfoodfacts_api("7891167011770") is None  # digito invalido


def test_sincronizar_gtins_existentes(db_catalogo):
    import sqlite3

    from cupomzinho.catalogo import inserir_produtos, sincronizar_gtins_existentes

    # Insere produto no catálogo de teste
    inserir_produtos(
        db_catalogo,
        [
            {
                "gtin": "7891000100103",
                "nome": "LEITE PIRACANJUBA",
                "marca": "PIRACANJUBA",
                "nutriscore": "B",
                "nova_group": 1,
            }
        ],
    )

    # Cria banco principal em memória
    con_p = sqlite3.connect(":memory:")
    con_p.row_factory = sqlite3.Row
    con_p.execute("""
        CREATE TABLE produto (
            id INTEGER PRIMARY KEY,
            descricao_canonica TEXT,
            gtin TEXT,
            marca TEXT,
            nutriscore TEXT,
            nova_group INTEGER,
            foto_url TEXT
        )
    """)
    con_p.execute(
        "INSERT INTO produto (id, descricao_canonica, gtin) VALUES (1, 'LEITE 1L', '7891000100103')"
    )
    con_p.commit()

    res = sincronizar_gtins_existentes(con_p, db_catalogo, limite=10)
    assert res["atualizados"] == 1
    assert res["total"] == 1

    prod = con_p.execute("SELECT * FROM produto WHERE id = 1").fetchone()
    assert prod["marca"] == "PIRACANJUBA"
    assert prod["nutriscore"] == "B"
    assert prod["nova_group"] == 1

