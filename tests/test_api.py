"""A API de ponta a ponta, com o cupom real semeado no banco.

Nenhum teste aqui toca a rede: o cupom entra pela camada de gravação, e o que
se exercita são as rotas de leitura, a serialização e o portão de identidade.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cupomzinho.cupons import gravar
from cupomzinho.nfce_pr import interpretar

FIXTURE = Path(__file__).parent / "fixtures" / "cupom_lar_2026-08-14.html"


def _app(tmp_path, monkeypatch, *, exigir_tailnet: bool):
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "true" if exigir_tailnet else "false")
    from cupomzinho.config import config
    config.cache_clear()
    from cupomzinho.main import app
    return app


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    from cupomzinho.config import config
    with TestClient(_app(tmp_path, monkeypatch, exigir_tailnet=False)) as c:
        cupom = interpretar(FIXTURE.read_text(encoding="utf-8"))
        gravar(c.app.state.con, cupom, morador="Miguel")
        yield c
    config.cache_clear()


def test_saude_nao_exige_identidade(cliente):
    r = cliente.get("/api/saude")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "cupons": 1}


def test_gastos_do_mes(cliente):
    agosto = cliente.get("/api/gastos?mes=2026-08").json()
    assert agosto["resumo"]["total"] == "106.74"    # string, não float
    assert agosto["resumo"]["cupons"] == 1
    assert agosto["resumo"]["por_loja"][0]["loja"] == "LAR COOPERATIVA AGROINDUSTRIAL"
    assert agosto["resumo"]["por_morador"][0]["morador"] == "Miguel"
    assert agosto["meses"][0]["mes"] == "2026-08"


def test_dinheiro_sai_como_string_e_nao_como_float(cliente):
    """Float no JSON reintroduziria o erro de centavo que o banco evita."""
    bruto = cliente.get("/api/gastos?mes=2026-08").text
    assert '"total": "106.74"' in bruto      # com aspas: é string
    assert '"total": 106.74' not in bruto    # sem aspas seria float


def test_lista_de_cupons(cliente):
    corpo = cliente.get("/api/cupons").json()
    assert len(corpo["cupons"]) == 1
    assert corpo["cupons"][0]["itens"] == 16
    assert corpo["revisao"] == []


def test_cupom_por_chave(cliente):
    chave = cliente.get("/api/cupons").json()["cupons"][0]["chave"]
    cupom = cliente.get(f"/api/cupom/{chave}").json()
    assert len(cupom["itens"]) == 16
    assert cupom["valor_pago"] == "106.74"
    assert cliente.get("/api/cupom/" + "0" * 44).status_code == 404


def test_produtos_e_historico(cliente):
    produtos = cliente.get("/api/produtos").json()["produtos"]
    assert len(produtos) == 14
    assert all(p["gtin"] is None for p in produtos)   # o cupom não traz GTIN

    atum = next(p for p in produtos if p["descricao"].startswith("ATUM"))
    detalhe = cliente.get(f"/api/produto/{atum['id']}").json()
    assert detalhe["menor_pago"] == detalhe["maior_pago"] == "10.97"
    assert len(detalhe["compras"]) == 2               # comprado duas vezes
    assert len(detalhe["serie"]) == 1                 # mas uma observação de preço


def test_busca_de_produto(cliente):
    achados = cliente.get("/api/produtos?busca=frimesa").json()["produtos"]
    assert {p["descricao"] for p in achados} == {
        "IOG FRIMESA PROT BAUNILHA 170G", "IOG FRIMESA PROT BAN ACAI 170G",
        "IOG FRIMESA PROT MGO 170G", "LEITE FRIMESA ZERO LACTOSE 1LT",
        "LEITE FRIMESA INT 1LT"}


def test_reenvio_por_chave_ja_guardada(cliente):
    chave = cliente.get("/api/cupons").json()["cupons"][0]["chave"]
    r = cliente.post("/api/cupom", json={"chave": chave})
    assert r.status_code == 200 and r.json()["novo"] is False


def test_chave_desconhecida_explica_que_precisa_do_link(cliente):
    r = cliente.post("/api/cupom", json={"chave": "1" * 44})
    assert r.status_code == 404
    assert "hash de segurança" in r.json()["detail"]


def test_link_sem_chave_e_recusado(cliente):
    r = cliente.post("/api/cupom", json={"url": "https://exemplo.com/?p=123"})
    assert r.status_code == 400


def test_fora_da_tailnet_leva_403(tmp_path, monkeypatch):
    """Com o portão ligado, quem não é morador não lê gasto da casa.

    O TestClient chega como "testclient", que não é 100.x — exatamente o caso
    de alguém batendo na porta por fora da tailnet.
    """
    from cupomzinho.config import config
    with TestClient(_app(tmp_path, monkeypatch, exigir_tailnet=True)) as c:
        assert c.get("/api/saude").status_code == 200     # a saúde é pública
        assert c.get("/api/gastos").status_code == 403
        assert c.get("/api/cupons").status_code == 403
        assert c.post("/api/cupom", json={"url": "x"}).status_code == 403
    config.cache_clear()


def test_a_pwa_e_servida(cliente):
    assert "cupomzinho" in cliente.get("/").text
    assert cliente.get("/app.js").status_code == 200
    assert cliente.get("/manifest.webmanifest").status_code == 200


def test_teto_de_consultas_vira_429_e_nao_500(cliente, monkeypatch):
    """Regressão: o teto estourando no canário derrubava a rota com 500."""
    from cupomzinho import menor_preco

    def estoura(self, **_kwargs):
        raise menor_preco.TetoAtingido("20 consultas na última hora. Libera às 15:20.")

    monkeypatch.setattr(menor_preco.MenorPreco, "buscar", estoura)
    lista = cliente.post("/api/listas", json={"nome": "x"}).json()["id"]
    resposta = cliente.post(f"/api/lista/{lista}/pesquisar")
    assert resposta.status_code == 429
    assert "Libera às" in resposta.json()["detail"]

    assert cliente.post("/api/enriquecer?quantos=1").status_code == 429
