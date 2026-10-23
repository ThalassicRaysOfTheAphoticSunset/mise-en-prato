"""Testes do módulo de receitas e ingredientes e da migração 011.

Cobre todo o ciclo de vida:
- Criação e obtenção de receitas com campos obrigatórios e opcionais.
- Adição de ingredientes com Decimal e inteiros (sem floats).
- Listagem e busca por nome.
- Integridade referencial (FK obrigatória e deleção em cascata).
- Rejeição estrita de valores em ponto flutuante (float).
- Cálculos puros em Decimal (escalonamento de porções).
- Preservação da integridade das tabelas legadas do sistema.
"""

import io
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from mise_en_prato.banco import conectar
from mise_en_prato.receitas import (
    Ingrediente,
    Receita,
    adicionar_ingrediente,
    atualizar_receita,
    buscar_receitas,
    criar_receita,
    escalar_receita,
    excluir_receita,
    formatar_fracao_culinaria,
    listar_categorias,
    listar_receitas,
    obter_receita,
    parse_linha_ingrediente,
    parse_texto_ingredientes,
    remover_foto_receita,
    remover_ingrediente,
    salvar_foto_receita,
)


@pytest.fixture
def con(tmp_path):
    """Conexão isolada com o banco SQLite migrado em modo WAL e com foreign keys."""
    banco = tmp_path / "teste_receitas.db"
    conexao = conectar(banco)
    yield conexao
    conexao.close()


def test_criar_receita_simples(con):
    rec_id = criar_receita(con, nome="Omelete Rápida")
    assert isinstance(rec_id, int)
    assert rec_id > 0

    receita = obter_receita(con, rec_id)
    assert receita is not None
    assert receita.id == rec_id
    assert receita.nome == "Omelete Rápida"
    assert receita.porcoes == 1
    assert receita.tempo_preparo_min is None
    assert receita.instrucoes is None
    assert len(receita.ingredientes) == 0
    assert receita.criada_em is not None


def test_criar_receita_completa(con):
    rec_id = criar_receita(
        con,
        nome="Bolo de Cenoura com Cobertura",
        porcoes=8,
        tempo_preparo_min=50,
        instrucoes="Bata no liquidificador a cenoura, o óleo e os ovos. Misture a farinha e asse.",
    )
    receita = obter_receita(con, rec_id)
    assert receita is not None
    assert receita.nome == "Bolo de Cenoura com Cobertura"
    assert receita.porcoes == 8
    assert receita.tempo_preparo_min == 50
    assert "liquidificador" in receita.instrucoes


def test_adicionar_ingredientes_decimal_e_inteiro(con):
    rec_id = criar_receita(con, nome="Panqueca Americana", porcoes=4)

    # 1. Quantidade inteira (ex: 2 ovos)
    id_ovo = adicionar_ingrediente(
        con, receita_id=rec_id, nome_ingrediente="Ovos", quantidade=2, unidade="unidade"
    )
    # 2. Quantidade Decimal com fração (ex: 1.5 xícara de leite)
    id_leite = adicionar_ingrediente(
        con,
        receita_id=rec_id,
        nome_ingrediente="Leite",
        quantidade=Decimal("1.5"),
        unidade="xícara",
    )
    # 3. Quantidade em gramas (ex: 250 g de farinha)
    id_farinha = adicionar_ingrediente(
        con,
        receita_id=rec_id,
        nome_ingrediente="Farinha de Trigo",
        quantidade=Decimal("250"),
        unidade="g",
    )
    # 4. Quantidade fracionária pequena (ex: 0.5 colher de sal)
    id_sal = adicionar_ingrediente(
        con,
        receita_id=rec_id,
        nome_ingrediente="Sal",
        quantidade=Decimal("0.5"),
        unidade="colher de chá",
    )

    assert all(i > 0 for i in (id_ovo, id_leite, id_farinha, id_sal))

    receita = obter_receita(con, rec_id)
    assert len(receita.ingredientes) == 4

    ovos = receita.ingredientes[0]
    assert ovos.nome_ingrediente == "Ovos"
    assert ovos.quantidade == Decimal("2")
    assert ovos.quantidade == 2
    assert ovos.unidade == "unidade"
    assert ovos.quantidade_centavos == 200

    leite = receita.ingredientes[1]
    assert leite.nome_ingrediente == "Leite"
    assert leite.quantidade == Decimal("1.5")
    assert leite.unidade == "xícara"
    assert leite.quantidade_centavos == 150

    farinha = receita.ingredientes[2]
    assert farinha.nome_ingrediente == "Farinha de Trigo"
    assert farinha.quantidade == Decimal("250")
    assert farinha.quantidade == 250
    assert farinha.unidade == "g"
    assert farinha.quantidade_centavos == 25000

    sal = receita.ingredientes[3]
    assert sal.nome_ingrediente == "Sal"
    assert sal.quantidade == Decimal("0.5")
    assert sal.unidade == "colher de chá"
    assert sal.quantidade_centavos == 50


def test_adicionar_ingrediente_com_gtin(con):
    rec_id = criar_receita(con, nome="Café com Leite Especial")
    adicionar_ingrediente(
        con,
        receita_id=rec_id,
        nome_ingrediente="Café Solúvel Nescafé",
        quantidade=Decimal("50"),
        unidade="g",
        gtin_associado="7891000100103",
    )

    receita = obter_receita(con, rec_id)
    assert len(receita.ingredientes) == 1
    ing = receita.ingredientes[0]
    assert ing.gtin_associado == "7891000100103"


def test_criar_receita_com_ingredientes_embutidos(con):
    rec_id = criar_receita(
        con,
        nome="Guacamole",
        porcoes=2,
        ingredientes=[
            {"nome_ingrediente": "Abacate", "quantidade": 1, "unidade": "unidade"},
            {"nome_ingrediente": "Tomate", "quantidade": Decimal("1.5"), "unidade": "unidade"},
            {"nome_ingrediente": "Suco de Limão", "quantidade": Decimal("15"), "unidade": "ml"},
        ],
    )

    receita = obter_receita(con, rec_id)
    assert receita is not None
    assert len(receita.ingredientes) == 3
    assert receita.ingredientes[0].nome_ingrediente == "Abacate"
    assert receita.ingredientes[1].quantidade == Decimal("1.5")
    assert receita.ingredientes[2].unidade == "ml"


def test_adicionar_ingrediente_objeto(con):
    rec_id = criar_receita(con, nome="Pão de Queijo")
    ing = Ingrediente(
        id=None,
        receita_id=rec_id,
        nome_ingrediente="Polvilho Azedo",
        quantidade=Decimal("500"),
        unidade="g",
        gtin_associado="7898900000001",
    )
    ing_id = adicionar_ingrediente(con, rec_id, ing)
    assert ing_id > 0

    receita = obter_receita(con, rec_id)
    assert len(receita.ingredientes) == 1
    salvo = receita.ingredientes[0]
    assert salvo.nome_ingrediente == "Polvilho Azedo"
    assert salvo.quantidade == Decimal("500")
    assert salvo.gtin_associado == "7898900000001"


def test_listar_receitas(con):
    criar_receita(con, nome="Receita A", porcoes=2)
    rec_b = criar_receita(con, nome="Receita B", porcoes=4)
    adicionar_ingrediente(
        con, rec_b, "Ingrediente B1", Decimal("100"), "g"
    )

    receitas = listar_receitas(con)
    assert len(receitas) >= 2
    nomes = [r.nome for r in receitas]
    assert "Receita A" in nomes
    assert "Receita B" in nomes

    rec_b_encontrada = next(r for r in receitas if r.nome == "Receita B")
    assert len(rec_b_encontrada.ingredientes) == 1
    assert rec_b_encontrada.ingredientes[0].nome_ingrediente == "Ingrediente B1"


def test_buscar_receitas(con):
    criar_receita(con, nome="Risoto de Cogumelos")
    criar_receita(con, nome="Risoto de Queijo Brie")
    criar_receita(con, nome="Sopa de Mandioquinha")

    encontradas = buscar_receitas(con, "Risoto")
    assert len(encontradas) == 2
    for r in encontradas:
        assert "Risoto" in r.nome

    vazio = buscar_receitas(con, "Inexistente xyz")
    assert len(vazio) == 0


def test_obter_receita_inexistente(con):
    assert obter_receita(con, 999999) is None


def test_proibicao_estrita_de_float(con):
    rec_id = criar_receita(con, nome="Receita Sem Float")

    # Quantidade float deve ser expressamente proibida
    with pytest.raises(TypeError, match=r"(?i)float"):
        adicionar_ingrediente(con, rec_id, "Açúcar", quantidade=1.5, unidade="xícara")

    with pytest.raises(TypeError, match=r"(?i)float"):
        Ingrediente(
            id=None,
            receita_id=rec_id,
            nome_ingrediente="Açúcar",
            quantidade=2.5,  # type: ignore[arg-type]
            unidade="xícara",
        )

    # Porções ou tempo como float também devem ser barrados
    with pytest.raises(TypeError, match=r"(?i)float"):
        criar_receita(con, nome="Receita Porção Float", porcoes=2.5)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match=r"(?i)float"):
        criar_receita(con, nome="Receita Tempo Float", tempo_preparo_min=30.5)  # type: ignore[arg-type]


def test_integridade_referencial_fk_invalida(con):
    # Inserir ingrediente para uma receita inexistente deve estourar erro de FK
    with pytest.raises(sqlite3.IntegrityError):
        adicionar_ingrediente(
            con,
            receita_id=888888,
            nome_ingrediente="Ingrediente Fantasma",
            quantidade=Decimal("100"),
            unidade="g",
        )


def test_integridade_referencial_on_delete_cascade(con):
    rec_id = criar_receita(con, nome="Receita Para Deletar")
    adicionar_ingrediente(con, rec_id, "Item 1", Decimal("100"), "g")
    adicionar_ingrediente(con, rec_id, "Item 2", Decimal("200"), "ml")

    assert con.execute(
        "SELECT COUNT(*) FROM ingredientes WHERE receita_id = ?", (rec_id,)
    ).fetchone()[0] == 2

    # Exclui a receita
    sucesso = excluir_receita(con, rec_id)
    assert sucesso is True
    assert obter_receita(con, rec_id) is None

    # Todos os ingredientes da receita devem sumir automaticamente em cascata
    qtd_ingredientes_restantes = con.execute(
        "SELECT COUNT(*) FROM ingredientes WHERE receita_id = ?", (rec_id,)
    ).fetchone()[0]
    assert qtd_ingredientes_restantes == 0


def test_remover_ingrediente(con):
    rec_id = criar_receita(con, nome="Receita Ajuste")
    ing_id = adicionar_ingrediente(con, rec_id, "Sal em excesso", Decimal("50"), "g")
    assert len(obter_receita(con, rec_id).ingredientes) == 1

    removido = remover_ingrediente(con, ing_id)
    assert removido is True
    assert len(obter_receita(con, rec_id).ingredientes) == 0


def test_calculo_escalar_porcoes_sem_float(con):
    rec_id = criar_receita(con, nome="Torta de Maçã", porcoes=4)
    adicionar_ingrediente(con, rec_id, "Farinha", Decimal("200"), "g")
    adicionar_ingrediente(con, rec_id, "Ovos", 2, "unidade")
    adicionar_ingrediente(con, rec_id, "Açúcar", Decimal("1.5"), "xícara")

    receita_base = obter_receita(con, rec_id)

    # Dobra para 8 porções
    receita_dobrada = escalar_receita(receita_base, novas_porcoes=8)
    assert receita_dobrada.porcoes == 8
    assert isinstance(receita_dobrada.ingredientes[0].quantidade, Decimal)
    assert receita_dobrada.ingredientes[0].quantidade == Decimal("400")
    assert receita_dobrada.ingredientes[1].quantidade == Decimal("4")
    assert receita_dobrada.ingredientes[2].quantidade == Decimal("3")

    # Reduz para 2 porções
    receita_reduzida = escalar_receita(receita_base, novas_porcoes=2)
    assert receita_reduzida.porcoes == 2
    assert receita_reduzida.ingredientes[0].quantidade == Decimal("100")
    assert receita_reduzida.ingredientes[1].quantidade == Decimal("1")
    assert receita_reduzida.ingredientes[2].quantidade == Decimal("0.75")


def test_indices_banco_criados(con):
    indices = {
        row["name"]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    assert "idx_receitas_nome" in indices
    assert "idx_ingredientes_receita" in indices
    assert "idx_ingredientes_nome" in indices
    assert "idx_ingredientes_gtin" in indices
    assert "idx_receitas_categoria" in indices


def test_criar_e_filtrar_por_categoria(con):
    criar_receita(con, nome="Brigadeiro", categoria="Sobremesa")
    criar_receita(con, nome="Pudim de Leite", categoria="Sobremesa")
    criar_receita(con, nome="Frango Grelhado", categoria="Almoço")

    sobremesas = listar_receitas(con, categoria="Sobremesa")
    assert len(sobremesas) == 2
    assert all(r.categoria == "Sobremesa" for r in sobremesas)

    almocos = listar_receitas(con, categoria="Almoço")
    assert len(almocos) == 1
    assert almocos[0].nome == "Frango Grelhado"

    cats = listar_categorias(con)
    assert cats == ["Almoço", "Sobremesa"]


def test_atualizar_receita(con):
    rec_id = criar_receita(
        con,
        nome="Sopa Simples",
        porcoes=2,
        ingredientes=[{"nome_ingrediente": "Batata", "quantidade": 2, "unidade": "unidade"}],
    )

    atualizada = atualizar_receita(
        con,
        receita_id=rec_id,
        nome="Sopa Rica",
        porcoes=4,
        tempo_preparo_min=35,
        instrucoes="Cozinhe tudo bem macio.",
        categoria="Jantar",
        ingredientes=[
            {"nome_ingrediente": "Batata", "quantidade": 4, "unidade": "unidade"},
            {"nome_ingrediente": "Cenoura", "quantidade": 2, "unidade": "unidade"},
        ],
    )

    assert atualizada.nome == "Sopa Rica"
    assert atualizada.porcoes == 4
    assert atualizada.tempo_preparo_min == 35
    assert atualizada.categoria == "Jantar"
    assert len(atualizada.ingredientes) == 2
    assert atualizada.ingredientes[1].nome_ingrediente == "Cenoura"


def test_parser_ingredientes_linhas():
    # Fração composta
    p1 = parse_linha_ingrediente("1 1/2 xícara de farinha de trigo")
    assert p1 is not None
    assert p1["quantidade"] == Decimal("1.5")
    assert p1["unidade"] == "xícara"
    assert p1["nome_ingrediente"] == "farinha de trigo"

    # Quantidade colada com unidade
    p2 = parse_linha_ingrediente("500g de carne moída")
    assert p2 is not None
    assert p2["quantidade"] == Decimal("500")
    assert p2["unidade"] == "g"
    assert p2["nome_ingrediente"] == "carne moída"

    # Unidades com fração simples e unicode
    p3 = parse_linha_ingrediente("½ colher de sopa de açúcar")
    assert p3 is not None
    assert p3["quantidade"] == Decimal("0.5")
    assert p3["unidade"] == "colher de sopa"
    assert p3["nome_ingrediente"] == "açúcar"

    # A gosto
    p4 = parse_linha_ingrediente("Sal e pimenta a gosto")
    assert p4 is not None
    assert p4["unidade"] == "a gosto"
    assert "Sal e pimenta" in p4["nome_ingrediente"]

    # Marcador de lista com número inteiro
    p5 = parse_linha_ingrediente("- 3 ovos caipiras")
    assert p5 is not None
    assert p5["quantidade"] == Decimal("3")
    assert p5["unidade"] == "unidade"
    assert p5["nome_ingrediente"] == "ovos caipiras"


def test_parser_texto_completo():
    texto = """
    2 xícaras de leite
    1/2 colher de chá de baunilha
    3 ovos
    Sal a gosto
    """
    itens = parse_texto_ingredientes(texto)
    assert len(itens) == 4
    assert itens[0]["nome_ingrediente"] == "leite"
    assert itens[0]["quantidade"] == Decimal("2")
    assert itens[1]["unidade"] == "colher de chá"
    assert itens[2]["quantidade"] == Decimal("3")


def test_formatar_fracoes_culinarias():
    assert formatar_fracao_culinaria(Decimal("0.5")) == "½"
    assert formatar_fracao_culinaria(Decimal("1.5")) == "1 ½"
    assert formatar_fracao_culinaria(Decimal("0.25")) == "¼"
    assert formatar_fracao_culinaria(Decimal("0.75")) == "¾"
    assert formatar_fracao_culinaria(Decimal("2")) == "2"
    assert formatar_fracao_culinaria(Decimal("250")) == "250"


def test_salvar_e_remover_foto_receita(con, tmp_path):
    pasta_fotos = tmp_path / "fotos_teste"
    rec_id = criar_receita(con, nome="Bolo com Foto")

    # Cria imagem sintética
    img = Image.new("RGB", (300, 200), color=(200, 50, 50))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG")
    bytes_foto = buffer.getvalue()

    foto_rel = salvar_foto_receita(con, pasta_fotos, rec_id, bytes_foto)
    assert foto_rel.endswith(".webp")

    # Verifica se arquivo existe no disco
    arquivo_disco = pasta_fotos / Path(foto_rel).name
    assert arquivo_disco.exists()
    assert arquivo_disco.stat().st_size > 0

    # Verifica se receita no banco aponta para a foto
    rec_banco = obter_receita(con, rec_id)
    assert rec_banco.foto_caminho == foto_rel

    # Remove foto
    sucesso = remover_foto_receita(con, pasta_fotos, rec_id)
    assert sucesso is True
    assert not arquivo_disco.exists()
    assert obter_receita(con, rec_id).foto_caminho is None


def test_excluir_receita_com_foto(con, tmp_path):
    pasta_fotos = tmp_path / "fotos_teste2"
    rec_id = criar_receita(con, nome="Receita Para Deletar com Foto")

    img = Image.new("RGB", (100, 100), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    salvar_foto_receita(con, pasta_fotos, rec_id, buf.getvalue())

    assert len(list(pasta_fotos.glob("*.webp"))) == 1

    # Exclui receita com limpeza de foto
    excluir_receita(con, rec_id, pasta_fotos=pasta_fotos)
    assert obter_receita(con, rec_id) is None
    assert len(list(pasta_fotos.glob("*.webp"))) == 0


def test_criar_receita_a_partir_de_objeto(con):
    obj = Receita(
        id=None,
        nome="Salada Simples",
        porcoes=2,
        tempo_preparo_min=10,
        instrucoes="Misture as folhas e tempere.",
    )
    rec_id = criar_receita(con, obj)
    assert rec_id > 0
    salva = obter_receita(con, rec_id)
    assert salva is not None
    assert salva.nome == "Salada Simples"
    assert salva.porcoes == 2


def test_preservacao_tabelas_legadas(con):
    """Garante que a nova migração não interferiu nas tabelas existentes."""
    tabelas = {
        row["name"]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    tabelas_esperadas = {
        "estabelecimento",
        "produto",
        "cupom",
        "item",
        "apelido_loja",
        "preco",
        "lista",
        "lista_item",
        "produto_variante",
        "codigo_loja_produto",
        "receitas",
        "ingredientes",
    }
    assert tabelas_esperadas.issubset(tabelas)

    # Inserção de produto legado continua válida
    con.execute(
        "INSERT INTO produto (descricao_canonica, acompanhar) VALUES ('ARROZ BRANCO 5KG', 1)"
    )
    cursor = con.execute(
        "SELECT id FROM produto WHERE descricao_canonica = 'ARROZ BRANCO 5KG'"
    )
    p_id = cursor.fetchone()[0]
    assert p_id > 0
