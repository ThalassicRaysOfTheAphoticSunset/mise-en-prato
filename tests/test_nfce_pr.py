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
