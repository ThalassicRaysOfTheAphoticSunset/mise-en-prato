"""Testes do leitor de NFC-e, contra um cupom real da casa.

A fixture é a compra de 14/08/2026 no LAR de Medianeira: 16 itens, R$ 106,74.
Ela foi anonimizada antes de entrar no repositório — o nome do operador de
caixa saiu, e a chave de acesso e o protocolo foram mascarados preservando o
formato. O que se testa aqui é a leitura, não o conteúdo da compra.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from cupomzinho.nfce_pr import (
    ErroSefaz,
    buscar,
    chave_da_url,
    dados_da_chave,
    interpretar,
    normalizar,
    validar_chave,
)

FIXTURE = Path(__file__).parent / "fixtures" / "cupom_lar_2026-08-14.html"
URL_QR = ("https://www.fazenda.pr.gov.br/nfce/qrcode?p=41260877752293016191651170001637541131086428"
          "|2|1|1|1E778A47C5BAEDD69BFCD4A14F8CD2A19C5CD919")


@pytest.fixture(scope="module")
def cupom():
    return interpretar(FIXTURE.read_text(encoding="utf-8"))


def test_estabelecimento(cupom):
    e = cupom.estabelecimento
    assert e.razao == "LAR COOPERATIVA AGROINDUSTRIAL"
    assert e.cnpj == "77752293016191"          # 14 dígitos, sem pontuação
    assert e.logradouro == "AV RIO GRANDE DO SUL"
    assert e.numero == "2876"
    assert e.complemento == ""                 # campo vazio no meio do endereço
    assert e.bairro == "SAO CRISTOVAO"
    assert e.municipio == "MEDIANEIRA"
    assert e.uf == "PR"


def test_identificacao(cupom):
    assert len(cupom.chave) == 44 and cupom.chave.isdigit()
    assert cupom.numero == "163754"
    assert cupom.serie == "117"
    assert cupom.emitido_em == datetime(2026, 8, 14, 16, 0, 20)
    assert cupom.consumidor == "não identificado"


def test_totais_apesar_do_id_repetido(cupom):
    """Regressão da armadilha principal: id="linhaTotal" se repete em TODOS os
    totais. Ler por id devolveria o primeiro valor para todos os campos."""
    assert cupom.qtd_itens == 16
    assert cupom.total == Decimal("108.74")
    assert cupom.desconto == Decimal("2.00")
    assert cupom.valor_pago == Decimal("106.74")
    assert cupom.forma_pagamento == "Cartão de Crédito"


def test_itens(cupom):
    assert len(cupom.itens) == 16
    primeiro = cupom.itens[0]
    assert primeiro.seq == 1
    assert primeiro.descricao == "ATUM GOM COSTA RALADO NAT. 170G"
    assert primeiro.codigo_loja == "456641"     # código interno da loja, não é GTIN
    assert primeiro.unidade == "PT"
    assert primeiro.valor_unitario == Decimal("10.97")
    assert primeiro.valor_total == Decimal("10.97")


def test_linhas_repetidas_nao_sao_agregadas(cupom):
    """O mesmo produto comprado duas vezes vira DUAS linhas no cupom.

    Agregar aqui perderia a fidelidade ao documento; quem agrega é a tela.
    """
    um, dois = cupom.itens[0], cupom.itens[1]
    assert um.descricao == dois.descricao and um.codigo_loja == dois.codigo_loja
    assert (um.seq, dois.seq) == (1, 2)


def test_quantidade_fracionaria(cupom):
    """Item por peso vem com vírgula decimal: 0,256 KG de pepino."""
    pepino = next(i for i in cupom.itens if i.descricao.startswith("PEPINO"))
    assert pepino.quantidade == Decimal("0.256")
    assert pepino.unidade == "KG"
    assert pepino.valor_unitario == Decimal("9.49")
    assert pepino.valor_total == Decimal("2.43")


def test_aritmetica_fecha_e_cupom_esta_completo(cupom):
    somado = sum(i.valor_total for i in cupom.itens)
    assert somado == Decimal("108.74")
    assert somado - cupom.desconto == cupom.valor_pago
    assert cupom.avisos == []
    assert cupom.completo is True


def test_operador_de_caixa_nao_e_guardado(cupom):
    """O rodapé traz o nome completo do caixa — dado pessoal de terceiro.

    Nenhum campo do Cupom pode carregá-lo, nem por acidente num campo de texto
    corrido. A fixture já entra no repositório com o nome removido; este teste
    garante que o parser também não o resgataria de um cupom não anonimizado.
    """
    despejo = repr(cupom).upper()
    assert "OPERADOR" not in despejo
    assert "ANONIMIZADO" not in despejo


def test_cupom_vazio_avisa_em_vez_de_estourar():
    """Página inesperada não pode derrubar o envio: vira cupom incompleto."""
    cupom = interpretar("<html><body><p>indisponível</p></body></html>")
    assert cupom.completo is False
    assert cupom.itens == []
    assert any("chave" in a for a in cupom.avisos)


@pytest.mark.parametrize("entrada", [
    URL_QR,
    "41260877752293016191651170001637541131086428",
    "4126 0877 7522 9301 6191 6511 7000 1637 5411 3108 6428",
])
def test_chave_da_url(entrada):
    assert chave_da_url(entrada) == "41260877752293016191651170001637541131086428"


def test_chave_invalida_devolve_none():
    assert chave_da_url("https://exemplo.com/?p=123|2|1") is None
    assert chave_da_url("") is None


def test_normalizar_aproxima_cupom_e_menor_preco():
    """O cupom escreve "NAT." e a API do Menor Preço escreve "NAT"."""
    assert normalizar("ATUM GOM COSTA RALADO NAT. 170G") == "ATUM GOM COSTA RALADO NAT 170G"


def test_normalizar_nao_confunde_produtos_diferentes():
    """NAT e DEF são atuns diferentes, com GTIN diferente e o MESMO preço.

    Normalizar não pode aproximá-los — foi a armadilha encontrada na sondagem
    da API, e é a razão de o casamento exigir descrição idêntica.
    """
    assert normalizar("ATUM GOM COSTA RALADO NAT 170G") != normalizar(
        "ATUM GOM COSTA RALADO DEF 170G"
    )


# ── a mesma nota pela consulta completa (chave digitada) ─────────────────────
# A fixture é a compra de 27/08/2026 no LAR, a primeira da casa a entrar pela
# chave digitada: 9 itens, R$ 63,94. Anonimizada como a outra — chave e
# protocolo mascarados preservando o formato. A tela da consulta completa é o
# mesmo DANFE, exceto que a descrição do item vem em txtTit, não txtTit2 — foi
# o que mandou este cupom para a fila de revisão com "sem descrição" em tudo.
FIXTURE_CHAVE = Path(__file__).parent / "fixtures" / "cupom_lar_consulta_chave_2026-08-27.html"


@pytest.fixture(scope="module")
def cupom_da_consulta_completa():
    return interpretar(FIXTURE_CHAVE.read_text(encoding="utf-8"))


def test_consulta_completa_le_as_descricoes(cupom_da_consulta_completa):
    """Regressão: txtTit sem o 2 não pode virar nove "sem descrição"."""
    itens = cupom_da_consulta_completa.itens
    assert len(itens) == 9
    assert all(i.descricao for i in itens)
    primeiro = itens[0]
    assert primeiro.descricao == "BATATA PALHA GERIBA TRAD 100G"
    assert primeiro.codigo_loja == "139491"
    assert primeiro.valor_total == Decimal("7.49")


def test_consulta_completa_fecha_e_esta_completa(cupom_da_consulta_completa):
    cupom = cupom_da_consulta_completa
    somado = sum(i.valor_total for i in cupom.itens)
    assert somado - cupom.desconto == cupom.valor_pago == Decimal("63.94")
    assert cupom.avisos == []
    assert cupom.completo is True


def test_consulta_completa_e_reconhecida_como_danfe():
    """A tela da chave digitada tem as marcas do DANFE e dispensa parser novo."""
    from cupomzinho.consulta_chave import interpretar_resposta

    cupom = interpretar_resposta(FIXTURE_CHAVE.read_text(encoding="utf-8"))
    assert len(cupom.itens) == 9


# ── layout completo SPED (com GTIN e NCM) ────────────────────────────────────
FIXTURE_SPED = Path(__file__).parent / "fixtures" / "cupom_sped_completo.html"


@pytest.fixture(scope="module")
def cupom_sped():
    return interpretar(FIXTURE_SPED.read_text(encoding="utf-8"))


def test_sped_completo_identificacao(cupom_sped):
    assert cupom_sped.chave == "41260976430438000100650010000123451123456785"
    assert cupom_sped.numero == "12345"
    assert cupom_sped.serie == "1"
    assert cupom_sped.emitido_em == datetime(2026, 9, 15, 14, 30, 0)
    assert cupom_sped.protocolo == "141260001234567"


def test_sped_completo_estabelecimento(cupom_sped):
    e = cupom_sped.estabelecimento
    assert e.razao == "MERCADO MODELO LTDA"
    assert e.cnpj == "76430438000100"
    assert e.municipio == "MEDIANEIRA"
    assert e.uf == "PR"


def test_sped_completo_itens_com_gtin_e_ncm(cupom_sped):
    itens = cupom_sped.itens
    assert len(itens) == 3

    cafe = itens[0]
    assert cafe.descricao == "CAFE PILAO TRADICIONAL 500G"
    assert cafe.quantidade == Decimal("1.000")
    assert cafe.unidade == "UN"
    assert cafe.valor_unitario == Decimal("18.90")
    assert cafe.valor_total == Decimal("18.90")
    assert cafe.gtin == "7896005800119"
    assert cafe.ncm == "09012100"

    leite = itens[1]
    assert leite.descricao == "LEITE INTEGRAL TIROL 1L"
    assert leite.gtin == "7891000100103"
    assert leite.ncm == "04012010"

    maca = itens[2]
    assert maca.descricao == "MACA FUJI KG"
    assert maca.unidade == "KG"
    assert maca.gtin is None  # 'SEM GTIN' é ignorado
    assert maca.ncm == "08081000"


def test_sped_completo_totais_e_fechamento(cupom_sped):
    assert cupom_sped.total == Decimal("38.40")
    assert cupom_sped.desconto == Decimal("0.00")
    assert cupom_sped.valor_pago == Decimal("38.40")
    assert cupom_sped.forma_pagamento == "Cartão de Débito"
    assert cupom_sped.completo is True


# ── a chave que se confere sozinha ───────────────────────────────────────────
# Mesma chave mascarada da fixture, com o dígito verificador recalculado para
# fechar. O algoritmo foi conferido em 27/08/2026 contra os 8 cupons reais já
# guardados no banco da casa: 8 de 8, dígito calculado igual ao impresso.
CHAVE = "41260877752293016191651170001637541199999992"


def test_chave_boa_passa_no_digito_verificador():
    assert validar_chave(CHAVE)


def test_digito_trocado_e_pego():
    """É o erro de quem copia 44 números do papel, e sai de graça pegá-lo.

    Sem isto, dígito trocado só apareceria depois de gastar um captcha e uma
    consulta ao portal, para voltar como "nota não encontrada" — que manda a
    pessoa conferir a chave inteira em vez de dizer onde está o erro.
    """
    trocada = CHAVE[:20] + ("8" if CHAVE[20] != "8" else "7") + CHAVE[21:]
    assert not validar_chave(trocada)
    assert not validar_chave(CHAVE[:43] + ("0" if CHAVE[43] != "0" else "1"))


def test_chave_de_tamanho_errado_ou_com_letra_nao_passa():
    assert not validar_chave(CHAVE[:43])
    assert not validar_chave(CHAVE[:43] + "X")
    assert not validar_chave("")


def test_dados_da_chave_dizem_loja_mes_e_numero():
    """Os 44 dígitos são posicionais: dá para montar meio cupom sem consultar.

    Série e número saem daqui iguais aos que o parser lê do DANFE — conferido
    nos 8 cupons do banco da casa em 27/08/2026, 8 de 8.
    """
    dados = dados_da_chave(CHAVE)
    assert dados["uf"] == "41"                       # Paraná
    assert (dados["ano"], dados["mes"]) == (2026, 8)
    assert dados["cnpj"] == "77752293016191"
    assert dados["modelo"] == "65"                   # NFC-e
    assert (dados["serie"], dados["numero"]) == ("117", "163754")


def test_chave_da_url_higieniza_bom():
    """Garante que BOM (\ufeff) e espaços antes ou depois da URL/chave são limpos."""
    com_bom = "\ufeff" + URL_QR
    assert chave_da_url(com_bom) == "41260877752293016191651170001637541131086428"

    chave_com_bom = " \ufeff " + CHAVE + " "
    assert chave_da_url(chave_com_bom) == CHAVE


def test_buscar_higieniza_bom(monkeypatch):
    """buscar() deve remover BOM antes de disparar a requisição HTTP."""
    chamadas = []

    class RespostaMock:
        text = "<html>ok</html>"
        def raise_for_status(self):
            pass

    def mock_get(url, **kwargs):
        chamadas.append(url)
        return RespostaMock()

    import httpx
    monkeypatch.setattr(httpx, "get", mock_get)

    buscar("\ufeff" + URL_QR)
    assert chamadas == [URL_QR]


def test_interpretar_pagina_erro_sefaz():
    """HTML de erro da SEFAZ com flashMessage ou mensagem de falha levanta ErroSefaz."""
    html_flash = """
    <html><body>
        <div id="flashMessage">
            Atenção. Não foi possível executar a operação: Url do QRCode mal formatado
        </div>
    </body></html>
    """
    with pytest.raises(ErroSefaz, match="Url do QRCode mal formatado"):
        interpretar(html_flash)

    html_sem_id = "<html><body>Não foi possível executar a operação</body></html>"
    with pytest.raises(ErroSefaz, match="Não foi possível executar a operação"):
        interpretar(html_sem_id)


def test_buscar_pagina_erro_sefaz(monkeypatch):
    """buscar() deve levantar ErroSefaz se a resposta contiver página de erro."""
    class RespostaErroMock:
        text = '<div id="flashMessage">Erro na SEFAZ</div>'
        def raise_for_status(self):
            pass

    import httpx
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: RespostaErroMock())

    with pytest.raises(ErroSefaz, match="Erro na SEFAZ"):
        buscar(URL_QR)


def test_interpretar_pagina_erro_sefaz_variacoes():
    """Variações de caixa, sem acento ou classe flashMessage também levantam ErroSefaz."""
    # Minúsculas
    with pytest.raises(ErroSefaz, match="não foi possível executar a operação"):
        interpretar("<html><body>não foi possível executar a operação: falha interna</body></html>")

    # Sem acento
    with pytest.raises(ErroSefaz, match="Nao foi possivel executar a operacao"):
        interpretar("<html><body>Atenção: Nao foi possivel executar a operacao</body></html>")

    # Classe .flashMessage
    with pytest.raises(ErroSefaz, match="Erro no portal"):
        interpretar('<html><body><div class="flashMessage">Erro no portal</div></body></html>')

    # Erro de QRCode mal formatado
    with pytest.raises(ErroSefaz, match="Url do QRCode mal formatad"):
        interpretar('<html><body>Aviso: Url do QRCode mal formatada</body></html>')

