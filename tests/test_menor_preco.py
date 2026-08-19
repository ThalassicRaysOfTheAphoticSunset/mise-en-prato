"""O cliente do Menor Preço: disciplina, não só requisição.

Nenhum teste aqui toca a rede — a camada HTTP é injetada. O que se exercita é
o que protege: teto por hora, espaçamento, recusa de resposta degradada e
gravação com a loja identificada.
"""
import json
from pathlib import Path

import pytest

from cupomzinho.banco import conectar
from cupomzinho.cupons import gravar
from cupomzinho.menor_preco import (
    CANARIO,
    MenorPreco,
    RespostaSuspeita,
    TetoAtingido,
    guardar,
)
from cupomzinho.nfce_pr import interpretar

FIXTURES = Path(__file__).parent / "fixtures"
SADIO = json.loads((FIXTURES / "menor_preco_sadio.json").read_text(encoding="utf-8"))
DEGRADADO = json.loads((FIXTURES / "menor_preco_envenenado_gtin.json").read_text(encoding="utf-8"))


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("TETO_CONSULTAS_HORA", "3")
    from cupomzinho.config import config
    config.cache_clear()
    con = conectar(tmp_path / "teste.db")
    gravar(con, interpretar((FIXTURES / "cupom_lar_2026-08-14.html").read_text(encoding="utf-8")),
           morador="Miguel")
    yield con
    con.close()
    config.cache_clear()


def cliente(con, resposta):
    """Cliente com a rede trocada por uma resposta fixa e sem espera real."""
    chamadas = []

    def http(parametros):
        chamadas.append(parametros)
        return resposta() if callable(resposta) else resposta

    c = MenorPreco(con, http=http, dormir=lambda _s: None)
    c.chamadas = chamadas
    return c


def test_consulta_sadia_devolve_produtos(con):
    c = cliente(con, SADIO)
    achados = c.buscar(termo="leite frimesa")
    assert len(achados) == 37
    assert c.chamadas[0]["termo"] == "leite frimesa"
    assert c.chamadas[0]["raio"] == 15 and c.chamadas[0]["data"] == 7


def test_resposta_degradada_levanta_e_nao_devolve_nada(con):
    c = cliente(con, DEGRADADO)
    with pytest.raises(RespostaSuspeita) as erro:
        c.buscar(gtin="7891167011779")
    assert "fora do PR" in str(erro.value)


def test_toda_consulta_entra_no_diario(con):
    c = cliente(con, DEGRADADO)
    with pytest.raises(RespostaSuspeita):
        c.buscar(gtin="7891167011779")
    linha = con.execute("SELECT * FROM consulta_menor_preco").fetchone()
    assert linha["alvo"] == "7891167011779" and linha["confiavel"] == 0
    assert "ncm" in linha["motivos"]


def test_teto_por_hora_barra(con):
    """O teto é a regra que impede a app de virar o robô que o termo proíbe."""
    c = cliente(con, SADIO)
    for _ in range(3):                    # TETO_CONSULTAS_HORA=3 no fixture
        c.buscar(termo="leite")
    with pytest.raises(TetoAtingido, match="robô"):
        c.buscar(termo="leite")
    assert len(c.chamadas) == 3           # a quarta nem chegou a sair


def test_teto_conta_ate_as_recusadas(con):
    """Consulta que voltou degradada gastou requisição do mesmo jeito."""
    c = cliente(con, DEGRADADO)
    for _ in range(3):
        with pytest.raises(RespostaSuspeita):
            c.buscar(termo="leite")
    assert c.consultas_na_ultima_hora() == 3


def test_canario_reconhece_os_dois_estados(con):
    assert cliente(con, {"produtos": [], "total": 0}).canario() is True
    assert cliente(con, DEGRADADO).canario() is False


def test_canario_reprova_mesmo_resposta_estruturalmente_ok(con):
    """O canário é mais duro que o `avaliar`: com termo impossível, zero é o certo.

    Esta resposta passaria em todas as regras estruturais — ncm de 8 dígitos,
    uf PR, gtin válido — e ainda assim é mentira, porque o termo não existe.
    """
    plausivel = {"produtos": [
        {"desc": CANARIO, "ncm": "16042010", "gtin": "7891167011779",
         "estabelecimento": {"uf": "PR", "nm_emp": f"MERCADO {k}", "mun": "MEDIANEIRA"}}
        for k in range(5)], "total": 5}
    c = cliente(con, plausivel)
    assert c.canario() is False


def test_busca_pelo_nome_exato_nao_e_recusada(con):
    """Regressão do falso positivo: nome exato do produto não é eco."""
    resposta = {"produtos": [
        {"desc": "ATUM GOM COSTA RALADO NAT 170G", "ncm": "16042010",
         "gtin": "7891167011779", "valor": "10.97", "distkm": "1.0",
         "datahora": "2026-08-19T10:00:00.000Z",
         "estabelecimento": {"uf": "PR", "nm_emp": "LAR", "mun": "MEDIANEIRA",
                             "codigo": "abc"}}]}
    achados = cliente(con, resposta).buscar(termo="ATUM GOM COSTA RALADO NAT 170G")
    assert len(achados) == 1


def test_canario_usa_termo_que_nao_existe(con):
    c = cliente(con, {"produtos": [], "total": 0})
    c.canario()
    assert c.chamadas[0]["termo"] == CANARIO


def test_consultas_sao_espacadas(con):
    """Rajada é o que denuncia robô — o cliente espaça mesmo se quem chama não espaçou."""
    esperas = []
    c = MenorPreco(con, http=lambda _p: SADIO, dormir=esperas.append)
    c.buscar(termo="a")
    c.buscar(termo="b")
    assert esperas and esperas[-1] > 0


# ── gravação ─────────────────────────────────────────────────────────────────
def test_guardar_preco_com_loja_e_distancia(con):
    produto_id = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    novas = guardar(con, produto_id, SADIO["produtos"])
    assert novas == len(SADIO["produtos"])

    linha = con.execute(
        "SELECT * FROM preco WHERE fonte = 'menor_preco' ORDER BY unitario_centavos LIMIT 1"
    ).fetchone()
    assert linha["loja"] and linha["municipio"] == "MEDIANEIRA"
    assert linha["distancia_km"] is not None
    assert linha["codigo_menor_preco"]


def test_loja_conhecida_do_cupom_ganha_o_cnpj(con):
    """O LAR está nos cupons: o preço do Menor Preço tem de casar com o CNPJ dele."""
    produto_id = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    guardar(con, produto_id, SADIO["produtos"])
    lar = con.execute(
        "SELECT COUNT(*) FROM preco WHERE fonte='menor_preco' AND cnpj = '77752293016191'"
    ).fetchone()[0]
    assert lar > 0

    codigo = con.execute(
        "SELECT codigo_menor_preco FROM estabelecimento WHERE cnpj='77752293016191'").fetchone()[0]
    assert codigo, "o pareamento com o código opaco da API tem de ficar guardado"


def test_guardar_e_idempotente(con):
    produto_id = con.execute("SELECT id FROM produto LIMIT 1").fetchone()[0]
    guardar(con, produto_id, SADIO["produtos"])
    assert guardar(con, produto_id, SADIO["produtos"]) == 0
