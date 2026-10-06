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


def test_chave_desconhecida_manda_para_a_consulta_completa(cliente):
    """A chave sozinha não consulta a rota do QR — mas não é mais fim de linha.

    O hash de segurança continua sendo exigido lá; quem entra só com a chave
    entra pela consulta completa do portal, que cobra captcha.
    """
    r = cliente.post("/api/cupom", json={"chave": "1" * 44})
    assert r.status_code == 404
    assert "hash de segurança" in r.json()["detail"]
    assert "/api/cupom/chave" in r.json()["detail"]


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


# ── entrar pela chave, com o QR perdido ──────────────────────────────────────
# A chave da fixture com o dígito verificador refeito para fechar: é cupom
# inventado, com a forma do verdadeiro.
CHAVE_NOVA = "41260876430438011297650100001622251010241380"


def _com_dv(chave: str) -> str:
    from cupomzinho.nfce_pr import digito_verificador

    return chave[:43] + digito_verificador(chave)


def _desafio_falso():
    from cupomzinho.consulta_chave import Desafio

    return Desafio(id="desafio-de-teste", url="https://exemplo.invalido/consulta",
                   campos={}, cookies={}, imagem=b"imagem-de-mentira")


@pytest.fixture
def portal(monkeypatch):
    """O portal do estado, dublado. Nenhum teste daqui toca a rede."""
    from cupomzinho import main

    chamadas = []
    monkeypatch.setattr(main, "abrir_desafio", _desafio_falso)

    def consultar(desafio, chave, captcha):
        chamadas.append((chave, captcha))
        if captcha != "certo":
            from cupomzinho.consulta_chave import CaptchaRecusado
            raise CaptchaRecusado("nao")
        return FIXTURE.read_text(encoding="utf-8")

    monkeypatch.setattr(main, "consultar", consultar)
    return chamadas


def _pegar_desafio(cliente):
    return cliente.get("/api/chave/desafio").json()["desafio"]


def test_desafio_traz_a_imagem_do_captcha(cliente, portal):
    corpo = cliente.get("/api/chave/desafio").json()
    assert corpo["imagem"].startswith("data:image/")
    assert corpo["desafio"]


def test_chave_digitada_traz_o_cupom_inteiro(cliente, portal):
    """O caminho que salva cupom de QR manchado: 44 dígitos + captcha."""
    chave = _com_dv(CHAVE_NOVA)
    r = cliente.post("/api/cupom/chave", json={
        "chave": chave, "desafio": _pegar_desafio(cliente), "captcha": "certo"})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["novo"] is True
    assert len(corpo["cupom"]["itens"]) == 16       # a fixture inteira, item a item
    assert portal == [(chave, "certo")]


def test_chave_aceita_os_espacos_do_papel(cliente, portal):
    """No cupom a chave vem em grupos de quatro. Ninguém vai juntá-los na mão."""
    chave = _com_dv(CHAVE_NOVA)
    espacada = " ".join(chave[i:i + 4] for i in range(0, 44, 4))
    r = cliente.post("/api/cupom/chave", json={
        "chave": espacada, "desafio": _pegar_desafio(cliente), "captcha": "certo"})
    assert r.status_code == 200 and portal == [(chave, "certo")]


def test_digito_trocado_nem_chega_no_portal(cliente, portal):
    """Recusar antes de consultar é o que poupa o captcha de quem errou um número."""
    ruim = _com_dv(CHAVE_NOVA)[:43] + "9" if _com_dv(CHAVE_NOVA)[43] != "9" else \
        _com_dv(CHAVE_NOVA)[:43] + "8"
    r = cliente.post("/api/cupom/chave", json={
        "chave": ruim, "desafio": _pegar_desafio(cliente), "captcha": "certo"})
    assert r.status_code == 400
    assert "dígito verificador" in r.json()["detail"]
    assert portal == []


def test_captcha_errado_explica_e_nao_grava(cliente, portal):
    r = cliente.post("/api/cupom/chave", json={
        "chave": _com_dv(CHAVE_NOVA), "desafio": _pegar_desafio(cliente), "captcha": "errado"})
    assert r.status_code == 422 and "captcha" in r.json()["detail"]
    assert cliente.get("/api/cupom/" + _com_dv(CHAVE_NOVA)).status_code == 404


def test_captcha_usado_nao_serve_duas_vezes(cliente, portal):
    """O `sid` já gasto não volta: erro rende desafio novo, nunca o mesmo."""
    desafio = _pegar_desafio(cliente)
    cliente.post("/api/cupom/chave", json={
        "chave": _com_dv(CHAVE_NOVA), "desafio": desafio, "captcha": "errado"})
    r = cliente.post("/api/cupom/chave", json={
        "chave": _com_dv(CHAVE_NOVA), "desafio": desafio, "captcha": "certo"})
    assert r.status_code == 410


def test_cupom_ja_guardado_nao_gasta_captcha(cliente, portal):
    """Reenviar chave já guardada não pede nada ao portal — nem captcha, nem consulta."""
    chave = _com_dv(CHAVE_NOVA)
    cliente.post("/api/cupom/chave", json={
        "chave": chave, "desafio": _pegar_desafio(cliente), "captcha": "certo"})
    portal.clear()

    r = cliente.post("/api/cupom/chave", json={
        "chave": chave, "desafio": "nem-existe", "captcha": "nem-precisa"})
    assert r.status_code == 200 and r.json()["novo"] is False
    assert portal == []


# ── a rede de segurança: só a chave e o total ────────────────────────────────
def test_manual_registra_o_gasto_e_pede_revisao(cliente):
    chave = _com_dv(CHAVE_NOVA)
    r = cliente.post("/api/cupom/manual", json={
        "chave": chave, "total": "106,74", "emitido_em": "2026-08-24"})
    assert r.status_code == 200, r.text
    cupom = r.json()["cupom"]
    assert cupom["valor_pago"] == "106.74"          # string, não float
    assert cupom["itens"] == []
    assert cupom["avisos"]                          # entra na fila de revisão
    assert cliente.get("/api/cupons").json()["revisao"]


def test_manual_tira_a_loja_da_propria_chave(cliente):
    """O CNPJ mora dentro da chave — a loja não fica anônima nem sem consulta."""
    r = cliente.post("/api/cupom/manual", json={
        "chave": _com_dv(CHAVE_NOVA), "total": "10", "emitido_em": "2026-08-24"})
    assert r.json()["cupom"]["loja"] == "CNPJ 76.430.438/0112-97"


def test_manual_nao_rebatiza_loja_conhecida(cliente):
    """A LAR já está no banco pelo cupom da fixture, e o nome dela não se perde.

    `_garantir_estabelecimento` sobrescreve a razão social; sem o cuidado, um
    cupom digitado renomearia o mercado inteiro para "CNPJ ...".
    """
    chave = _com_dv("41260877752293016191651170009999991010241380")
    cliente.post("/api/cupom/manual",
                 json={"chave": chave, "total": "10", "emitido_em": "2026-08-24"})
    assert cliente.get("/api/cupom/" + chave).json()["loja"] == "LAR COOPERATIVA AGROINDUSTRIAL"


def test_manual_recusa_data_de_outro_mes(cliente):
    """Gasto no mês errado é pior que cupom faltando — e a chave sabe o mês."""
    r = cliente.post("/api/cupom/manual", json={
        "chave": _com_dv(CHAVE_NOVA), "total": "10", "emitido_em": "2026-07-24"})
    assert r.status_code == 400 and "08/2026" in r.json()["detail"]


def test_manual_recusa_total_que_nao_e_numero(cliente):
    r = cliente.post("/api/cupom/manual", json={
        "chave": _com_dv(CHAVE_NOVA), "total": "", "emitido_em": "2026-08-24"})
    assert r.status_code == 400


def test_listar_falhas_e_servir_foto(cliente, tmp_path):
    pasta_falhas = tmp_path / "falhas"
    pasta_falhas.mkdir(parents=True, exist_ok=True)
    foto = pasta_falhas / "2026-09-26_113651.jpg"
    foto.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 20)

    r = cliente.get("/api/falhas")
    assert r.status_code == 200
    falhas = r.json()["falhas"]
    assert len(falhas) == 1
    assert falhas[0]["arquivo"] == "2026-09-26_113651.jpg"

    r_foto = cliente.get("/api/falha/2026-09-26_113651.jpg")
    assert r_foto.status_code == 200
    assert r_foto.headers["content-type"].startswith("image/jpeg")
    assert len(r_foto.content) == 24

    r_invalido = cliente.get("/api/falha/.._etc_passwd")
    assert r_invalido.status_code in (400, 404)


def test_enviar_html_cupom(cliente):
    html = FIXTURE.read_text(encoding="utf-8")
    r = cliente.post(
        "/api/cupom/html",
        files={"arquivo": ("nota.html", html.encode("utf-8"), "text/html")},
    )
    assert r.status_code == 200
    dados = r.json()
    assert dados["cupom"]["loja"] == "LAR COOPERATIVA AGROINDUSTRIAL"
    assert len(dados["cupom"]["itens"]) > 0


def test_enviar_html_invalido(cliente):
    r = cliente.post(
        "/api/cupom/html",
        files={"arquivo": ("nota.html", b"<html><body>nada aqui</body></html>", "text/html")},
    )
    assert r.status_code == 400


def test_revisar_cupom_endpoint(cliente):
    # Criar um cupom incompleto no banco
    quebrado = interpretar("<html><body>indisponível</body></html>")
    quebrado.chave = "7" * 44
    quebrado.estabelecimento.cnpj = "12345678000190"
    quebrado.estabelecimento.razao = "SUPERMERCADO LOCAL"
    gravar(cliente.app.state.con, quebrado, morador="Miguel")

    # Verifica que está na fila de revisão
    corpo = cliente.get("/api/cupons").json()
    assert any(c["chave"] == quebrado.chave for c in corpo["revisao"])

    # Revisa com itens e aprova
    r = cliente.post(f"/api/cupom/{quebrado.chave}/revisar", json={
        "itens": [
            {"descricao": "CAFE EM PO 500G", "quantidade": "1", "unidade": "UN",
             "valor_unitario": "18.90", "valor_total": "18.90"},
            {"descricao": "ACUCAR CRISTAL 1KG", "quantidade": "2", "unidade": "UN",
             "valor_unitario": "4.50", "valor_total": "9.00"},
        ],
        "total": "27.90",
        "desconto": "0.00",
        "valor_pago": "27.90",
        "aprovar": True,
    })
    assert r.status_code == 200, r.text
    dados = r.json()
    assert dados["ok"] is True
    assert dados["cupom"]["completo"] is True
    assert len(dados["cupom"]["itens"]) == 2
    assert dados["cupom"]["valor_pago"] == "27.90"

    # Não está mais na fila de revisão
    corpo_depois = cliente.get("/api/cupons").json()
    assert not any(c["chave"] == quebrado.chave for c in corpo_depois["revisao"])


def test_gastos_inclui_perfil_nutricional(cliente):
    agosto = cliente.get("/api/gastos?mes=2026-08").json()
    assert "perfil_nutricional" in agosto["resumo"]
    nutri = agosto["resumo"]["perfil_nutricional"]
    assert "por_nova" in nutri
    assert "total_classificado" in nutri
    assert "percentual_ultraprocessados" in nutri


def test_catalogo_status_sem_inicializar(cliente):
    r = cliente.get("/api/catalogo/status")
    assert r.status_code == 200
    dados = r.json()
    assert dados["disponivel"] is False


def test_catalogo_status_e_enriquecimento_local(cliente, tmp_path):
    from cupomzinho.catalogo import conectar_catalogo, inserir_produtos
    from cupomzinho.config import config

    caminho_cat = config().banco_catalogo
    cat = conectar_catalogo(caminho_cat, criar=True)
    inserir_produtos(cat, [
        {
            "gtin": "7891167011779",
            "nome": "ATUM GOM COSTA RALADO NAT 170G",
            "marca": "GOMES DA COSTA",
            "nutriscore": "A",
            "nova_group": 3,
            "origem": "openfoodfacts",
        }
    ])
    cat.close()

    # Status agora deve reportar disponível
    r_status = cliente.get("/api/catalogo/status")
    assert r_status.status_code == 200
    dados_status = r_status.json()
    assert dados_status["disponivel"] is True
    assert dados_status["total"] == 1

    # Dispara enriquecimento local
    r_enriq = cliente.post("/api/enriquecer/local?quantos=50")
    assert r_enriq.status_code == 200
    dados_enriq = r_enriq.json()
    assert "identificados" in dados_enriq
    assert any(i["gtin"] == "7891167011779" for i in dados_enriq["identificados"])


def test_buscar_candidatos_e_definir_gtin_manual(cliente):
    # Pega um produto
    res = cliente.get("/api/produtos")
    assert res.status_code == 200
    prods = res.json()["produtos"]
    assert len(prods) > 0
    p = prods[0]

    # Busca candidatos
    r_cand = cliente.get(f"/api/produto/{p['id']}/candidatos")
    assert r_cand.status_code == 200
    dados_cand = r_cand.json()
    assert "candidatos" in dados_cand
    assert dados_cand["produto_id"] == p["id"]

    # Tentativa com GTIN inválido (dígito errado)
    r_invalido = cliente.post(f"/api/produto/{p['id']}/gtin", json={"gtin": "7891167011770"})
    assert r_invalido.status_code == 400

    # Atribuição com GTIN válido (Atum Gomes da Costa)
    r_valido = cliente.post(f"/api/produto/{p['id']}/gtin", json={"gtin": "7891167011779"})
    assert r_valido.status_code == 200
    dados_valido = r_valido.json()
    assert dados_valido["ok"] is True
    assert dados_valido["produto"]["gtin"] == "7891167011779"


def test_gerenciar_variantes_api(cliente):
    res = cliente.get("/api/produtos")
    assert res.status_code == 200
    p = res.json()["produtos"][0]
    pid = p["id"]

    # 1. Salva duas variantes
    dados_envio = {
        "variantes": [
            {
                "gtin": "7896045102556",
                "nome": "Bebida Láctea Cappuccino Classic 260ml",
                "sabor": "Classic",
                "marca": "3 Corações",
                "eh_padrao": True,
            },
            {
                "gtin": "7896045102563",
                "nome": "Bebida Láctea Cappuccino Chocolate 260ml",
                "sabor": "Chocolate",
                "marca": "3 Corações",
                "eh_padrao": False,
            },
        ],
        "gtin_padrao": "7896045102556",
    }
    r_salvar = cliente.post(f"/api/produto/{pid}/variantes", json=dados_envio)
    assert r_salvar.status_code == 200
    res_salvar = r_salvar.json()
    assert res_salvar["ok"] is True
    assert res_salvar["inseridos"] == 2

    # 2. Lista variantes via GET
    r_listar = cliente.get(f"/api/produto/{pid}/variantes")
    assert r_listar.status_code == 200
    vars_listadas = r_listar.json()["variantes"]
    assert len(vars_listadas) == 2
    assert vars_listadas[0]["gtin"] == "7896045102556"
    assert vars_listadas[0]["eh_padrao"] == 1

    # 3. Altera a variante padrão
    r_padrao = cliente.post(f"/api/produto/{pid}/variante/7896045102563/padrao")
    assert r_padrao.status_code == 200
    assert r_padrao.json()["ok"] is True
    assert r_padrao.json()["produto"]["gtin"] == "7896045102563"

    # 4. Remove uma variante
    r_del = cliente.delete(f"/api/produto/{pid}/variante/7896045102556")
    assert r_del.status_code == 200
    r_depois = cliente.get(f"/api/produto/{pid}/variantes")
    assert len(r_depois.json()["variantes"]) == 1



