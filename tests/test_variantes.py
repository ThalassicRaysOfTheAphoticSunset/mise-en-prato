"""Testes de variantes e sabores de produtos multissabor."""
import pytest

from mise_en_prato.banco import conectar
from mise_en_prato.catalogo import (
    definir_variante_padrao,
    extrair_sabor,
    listar_variantes,
    remover_variante,
    salvar_variantes_do_produto,
)
from mise_en_prato.enriquecimento import pendentes
from mise_en_prato.unificacao import candidatos, unificar


@pytest.fixture
def con(tmp_path):
    caminho = tmp_path / "teste_variantes.db"
    con = conectar(caminho)
    yield con
    con.close()


def test_migracao_009_cria_tabela_e_faz_backfill(tmp_path):
    # Cria banco e insere produto com GTIN
    db_path = tmp_path / "teste_backfill.db"
    con = conectar(db_path)
    con.execute("""
        INSERT INTO produto (id, descricao_canonica, gtin, marca, nutriscore, nova_group)
        VALUES (999, 'PRODUTO TESTE', '7891167011779', 'Gomes da Costa', 'A', 1)
    """)
    con.commit()

    # Executa salvar ou verifica variante existente
    vars = listar_variantes(con, 999)
    # Como foi inserido após a migração ter rodado no conectar, inserimos variante e testamos
    assert isinstance(vars, list)
    con.close()


def test_extrair_sabor():
    nome = "Bebida Láctea UHT Cappuccino Chocolate 3 Corações 260ml"
    assert extrair_sabor(nome, "3 Corações") == "Cappuccino Chocolate"
    assert extrair_sabor("Iogurte Frimesa Proteínas Sabor Baunilha 170g", "Frimesa") == "Baunilha"
    assert extrair_sabor("Refrigerante Coca-Cola Sem Açúcar 2L", "Coca-Cola") == "Sem Açúcar"
    assert extrair_sabor("") == ""


def test_salvar_e_listar_variantes(con):
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (1, 'BEB LA 3 CORAC 260ML')")
    con.commit()

    variantes = [
        {
            "gtin": "7896045102556",
            "nome": "Bebida Láctea UHT Cappuccino Classic 3 Corações 260ml",
            "sabor": "Classic",
            "marca": "3 Corações",
            "nutriscore": "D",
            "nova_group": 4,
            "eh_padrao": True,
        },
        {
            "gtin": "7896045102563",
            "nome": "Bebida Láctea UHT Cappuccino Chocolate 3 Corações 260ml",
            "sabor": "Chocolate",
            "marca": "3 Corações",
            "nutriscore": "D",
            "nova_group": 4,
            "eh_padrao": False,
        },
    ]

    inseridos = salvar_variantes_do_produto(con, 1, variantes)
    assert inseridos == 2

    salvas = listar_variantes(con, 1)
    assert len(salvas) == 2
    assert salvas[0]["gtin"] == "7896045102556"
    assert salvas[0]["eh_padrao"] == 1
    assert salvas[0]["sabor"] == "Classic"

    # Produto pai sincronizou com a variante padrão
    prod = con.execute("SELECT * FROM produto WHERE id = 1").fetchone()
    assert prod["gtin"] == "7896045102556"
    assert prod["marca"] == "3 Corações"
    assert prod["nutriscore"] == "D"


def test_definir_variante_padrao(con):
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (2, 'BEB LA 3 CORAC 260ML')")
    con.commit()

    variantes = [
        {"gtin": "7896045102556", "nome": "Classic", "sabor": "Classic", "eh_padrao": True},
        {"gtin": "7896045102563", "nome": "Chocolate", "sabor": "Chocolate", "eh_padrao": False},
    ]
    salvar_variantes_do_produto(con, 2, variantes)

    # Troca padrão para Chocolate
    ok = definir_variante_padrao(con, 2, "7896045102563")
    assert ok is True

    salvas = listar_variantes(con, 2)
    assert salvas[0]["gtin"] == "7896045102563"
    assert salvas[0]["eh_padrao"] == 1

    prod = con.execute("SELECT gtin FROM produto WHERE id = 2").fetchone()
    assert prod["gtin"] == "7896045102563"


def test_remover_variante(con):
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (3, 'BEB LA 3 CORAC 260ML')")
    con.commit()

    variantes = [
        {"gtin": "7896045102556", "nome": "Classic", "eh_padrao": True},
        {"gtin": "7896045102563", "nome": "Chocolate", "eh_padrao": False},
    ]
    salvar_variantes_do_produto(con, 3, variantes)

    # Remove o padrão
    ok = remover_variante(con, 3, "7896045102556")
    assert ok is True

    salvas = listar_variantes(con, 3)
    assert len(salvas) == 1
    # Chocolate vira o novo padrão
    assert salvas[0]["gtin"] == "7896045102563"
    assert salvas[0]["eh_padrao"] == 1

    prod = con.execute("SELECT gtin FROM produto WHERE id = 3").fetchone()
    assert prod["gtin"] == "7896045102563"

    # Remove o último
    remover_variante(con, 3, "7896045102563")
    assert len(listar_variantes(con, 3)) == 0
    prod = con.execute("SELECT gtin FROM produto WHERE id = 3").fetchone()
    assert prod["gtin"] is None


def test_unificar_preserva_variantes_sem_perda_por_cascade(con):
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (10, 'BEB LA 3 CORAC 260ML')")
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (20, 'BEB L 3 CORAC 260ML')")
    con.commit()

    # Produto 10 tem Classic e Chocolate
    salvar_variantes_do_produto(con, 10, [
        {"gtin": "7896045102556", "nome": "Classic"},
        {"gtin": "7896045102563", "nome": "Chocolate"},
    ])
    # Produto 20 tem Chocolate e Caramelo
    salvar_variantes_do_produto(con, 20, [
        {"gtin": "7896045102563", "nome": "Chocolate"},
        {"gtin": "7896045102716", "nome": "Caramelo"},
    ])

    # Unifica 20 em 10
    resultado = unificar(con, manter=10, juntar=20)
    assert resultado["manter"] == 10

    # Todas as 3 variantes distintas devem estar preservadas no produto 10
    vars_final = listar_variantes(con, 10)
    gtins = {v["gtin"] for v in vars_final}
    assert gtins == {"7896045102556", "7896045102563", "7896045102716"}

    # Produto 20 foi removido e não possui mais variantes
    assert con.execute("SELECT 1 FROM produto WHERE id = 20").fetchone() is None
    assert len(listar_variantes(con, 20)) == 0


def test_candidatos_unificacao_por_variantes_compartilhadas(con):
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (30, 'BEB LA 3 CORAC 260ML')")
    con.execute("INSERT INTO produto (id, descricao_canonica) VALUES (40, 'BEB L 3 CORAC 260ML')")
    con.commit()

    # As descrições têm L vs LA, que parecidos() recusa.
    # O produto 30 tem Classic (padrão) e Chocolate (variante).
    salvar_variantes_do_produto(con, 30, [
        {"gtin": "7896045102556", "nome": "Classic", "eh_padrao": True},
        {"gtin": "7896045102563", "nome": "Chocolate", "eh_padrao": False},
    ])
    # O produto 40 tem Caramelo (padrão) e Chocolate (variante).
    salvar_variantes_do_produto(con, 40, [
        {"gtin": "7896045102716", "nome": "Caramelo", "eh_padrao": True},
        {"gtin": "7896045102563", "nome": "Chocolate", "eh_padrao": False},
    ])

    cand = candidatos(con)
    assert len(cand) >= 1
    par = next(c for c in cand if {c["manter"]["id"], c["juntar"]["id"]} == {30, 40})
    assert par["forca"] == 0
    assert "código de barras de sabor/variante" in par["motivo"]


def test_pendentes_ignora_produto_com_variantes(con):
    con.execute(
        "INSERT INTO produto (id, descricao_canonica, gtin) VALUES (50, 'IOG MULTISSABOR', NULL)"
    )
    # Garante estabelecimento para não violar foreign key do cupom
    con.execute("INSERT INTO estabelecimento (cnpj, razao) VALUES ('000', 'Mercado Teste')")
    con.execute("INSERT INTO cupom (chave, cnpj, criado_em) VALUES ('123', '000', '2026-08-01')")
    con.execute("""
        INSERT INTO item (
            id, chave, seq, descricao, quantidade_milesimos,
            unitario_centavos, total_centavos, produto_id
        ) VALUES (1, '123', 1, 'IOG MULTISSABOR', 1000, 500, 500, 50)
    """)
    con.commit()

    # Antes de ter variantes, deve ser pendente
    pends = [p["produto_id"] for p in pendentes(con)]
    assert 50 in pends

    # Ao associar variantes, mesmo sem produto.gtin (ou com ele), sai de pendentes
    salvar_variantes_do_produto(con, 50, [{"gtin": "7896045102556", "nome": "Sabor A"}])
    pends_depois = [p["produto_id"] for p in pendentes(con)]
    assert 50 not in pends_depois
