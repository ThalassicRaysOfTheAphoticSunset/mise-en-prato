"""Testes do pipeline de OCR local de cupons com Tesseract e Pillow.

Verifica:
1. Pré-processamento de imagem (EXIF, escala, binarização).
2. Detecção e atalho para chave NFC-e de 44 dígitos com DV válido.
3. Extração estruturada de itens, CNPJ, estabelecimento e valores.
4. Regra dos centavos: fecha a conta -> completo = 1; diverge -> completo = 0 na fila de revisão.
5. Integração com banco de dados e fila de revisão.
"""
from decimal import Decimal
from io import BytesIO

import pytest
from PIL import Image, ImageDraw, ImageFont

from cupomzinho.banco import conectar
from cupomzinho.cupons import fila_de_revisao, gravar, ler
from cupomzinho.ocr import (
    interpretar_cupom_ocr,
    pre_processar_imagem,
    procurar_chave_nfce,
)

FONTE_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def _criar_imagem_cupom(linhas: list[str], largura: int = 600, tamanho_fonte: int = 20) -> bytes:
    """Gera imagem sintética de cupom térmico para testes determinísticos."""
    try:
        fonte = ImageFont.truetype(FONTE_PATH, tamanho_fonte)
    except OSError:
        fonte = ImageFont.load_default()

    altura = 40 + len(linhas) * (tamanho_fonte + 14)
    img = Image.new("RGB", (largura, altura), color="white")
    draw = ImageDraw.Draw(img)

    y = 20
    for linha in linhas:
        draw.text((20, y), linha, fill="black", font=fonte)
        y += tamanho_fonte + 14

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def con(tmp_path):
    c = conectar(tmp_path / "teste_ocr.db")
    yield c
    c.close()


def test_pre_processamento_devolve_imagem_em_tons_de_cinza():
    dados = _criar_imagem_cupom(["CUPOM DE TESTE", "TOTAL R$ 10,00"])
    img = Image.open(BytesIO(dados))
    processada = pre_processar_imagem(img)
    assert processada.mode == "L"
    assert processada.width > 0 and processada.height > 0


def test_procurar_chave_nfce_com_dv_valido():
    # Chave real de 44 dígitos com DV válido calculado (terminada em 2)
    chave_real = "41260877752293016191651170001637541199999992"
    texto_com_chave = (
        f"Consulte pela Chave de Acesso em www.fazenda.pr.gov.br\n{chave_real}\nProtocolo"
    )
    achada = procurar_chave_nfce(texto_com_chave)
    assert achada == chave_real

    # Chave com espaços/blocos de 4
    chave_formatada = "4126 0877 7522 9301 6191 6511 7000 1637 5411 9999 9992"
    assert procurar_chave_nfce(f"CHAVE: {chave_formatada}") == chave_real

    # Número qualquer de 44 dígitos com DV errado não deve ser aceito
    chave_falsa = "12345678901234567890123456789012345678901234"
    assert procurar_chave_nfce(chave_falsa) is None


def test_ocr_cupom_balanceado_fecha_centavos_e_fica_completo(con):
    linhas = [
        "PADARIA CENTRAL LTDA",
        "CNPJ: 12.345.678/0001-90",
        "EMISSAO: 10/05/2026 09:30:00",
        "------------------------------------",
        "001 PAO FRANCES KG 0,500 X 16,00 8,00",
        "002 LEITE INTEGRAL 1 UN X 5,50 5,50",
        "------------------------------------",
        "TOTAL R$ 13,50",
        "VALOR A PAGAR R$ 13,50",
        "FORMA PAGTO: PIX 13,50",
    ]
    foto_bytes = _criar_imagem_cupom(linhas)
    cupom = interpretar_cupom_ocr(foto_bytes)

    assert cupom.estabelecimento.cnpj == "12345678000190"
    assert "PADARIA CENTRAL" in cupom.estabelecimento.razao
    assert len(cupom.itens) == 2

    item1 = cupom.itens[0]
    assert "PAO FRANCES" in item1.descricao
    assert item1.quantidade == Decimal("0.500")
    assert item1.unidade == "KG"
    assert item1.valor_unitario == Decimal("16.00")
    assert item1.valor_total == Decimal("8.00")

    item2 = cupom.itens[1]
    assert "LEITE INTEGRAL" in item2.descricao
    assert item2.quantidade == Decimal("1.000")
    assert item2.valor_total == Decimal("5.50")

    assert cupom.total == Decimal("13.50")
    assert cupom.valor_pago == Decimal("13.50")
    assert cupom.completo is True
    assert cupom.avisos == []

    # Gravar no banco com origem 'foto'
    salvo = gravar(con, cupom, morador="Miguel", origem="foto")
    assert salvo is True

    lido = ler(con, cupom.chave)
    assert lido is not None
    assert lido["completo"] is True
    assert len(lido["itens"]) == 2
    assert lido["valor_pago"] == Decimal("13.50")
    # Não deve estar na fila de revisão
    assert fila_de_revisao(con) == []


def test_ocr_cupom_com_divergencia_de_centavos_vai_para_fila_de_revisao(con):
    linhas = [
        "FRUTARIA DO ZE",
        "CNPJ: 98.765.432/0001-10",
        "10/05/2026",
        "001 BANANA PRATA KG 1,000 X 6,00 6,00",
        "002 MACA FUJI KG 1,000 X 8,00 8,00",
        # Soma é 14,00, mas o papel declara Total R$ 20,00 (divergência de centavos)
        "TOTAL R$ 20,00",
        "VALOR A PAGAR R$ 20,00",
    ]
    foto_bytes = _criar_imagem_cupom(linhas)
    cupom = interpretar_cupom_ocr(foto_bytes)

    assert cupom.completo is False
    assert any("Divergência de centavos" in a or "difere" in a for a in cupom.avisos)

    salvo = gravar(con, cupom, morador="Miguel", origem="foto")
    assert salvo is True

    # Como completo = 0, deve constar na fila de revisão
    pendentes = fila_de_revisao(con)
    assert len(pendentes) == 1
    assert pendentes[0]["chave"] == cupom.chave


def test_ocr_itens_multilinha_e_desconto(con):
    linhas = [
        "MERCADINHO DA ESQUINA",
        "CNPJ: 11.222.333/0001-44",
        "05/06/2026 18:20",
        "------------------------------------",
        "001 ARROZ TIO JOAO 5KG",
        "1 UN X 26,00 26,00",
        "002 FEIJAO CARIOCA 1KG",
        "2 UN X 8,50 17,00",
        "------------------------------------",
        "SUBTOTAL R$ 43,00",
        "DESCONTO R$ 3,00",
        "TOTAL R$ 40,00",
        "PAGO R$ 40,00",
        "DINHEIRO",
    ]
    foto_bytes = _criar_imagem_cupom(linhas)
    cupom = interpretar_cupom_ocr(foto_bytes)

    assert cupom.estabelecimento.cnpj == "11222333000144"
    assert len(cupom.itens) == 2
    assert "ARROZ TIO JOAO" in cupom.itens[0].descricao
    assert cupom.itens[0].valor_total == Decimal("26.00")
    assert "FEIJAO CARIOCA" in cupom.itens[1].descricao
    assert cupom.itens[1].valor_total == Decimal("17.00")
    assert cupom.desconto == Decimal("3.00")
    assert cupom.total == Decimal("43.00")
    assert cupom.valor_pago == Decimal("40.00")
    assert cupom.completo is True
    assert cupom.forma_pagamento == "DINHEIRO"


def test_api_upload_foto_aciona_ocr_quando_nao_ha_qr(tmp_path, monkeypatch):
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    from cupomzinho.config import config
    config.cache_clear()
    from fastapi.testclient import TestClient

    from cupomzinho.main import app

    linhas = [
        "PADARIA ARTESANAL",
        "CNPJ: 55.444.333/0001-22",
        "12/07/2026 08:15",
        "001 BROA DE MILHO 1 UN X 7,00 7,00",
        "TOTAL R$ 7,00",
        "VALOR A PAGAR R$ 7,00",
    ]
    foto_bytes = _criar_imagem_cupom(linhas)

    with TestClient(app) as cliente:
        r = cliente.post(
            "/api/cupom/foto",
            files={"foto": ("recibo.jpg", foto_bytes, "image/jpeg")},
        )
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["origem"] == "foto"
        assert corpo["ocr"] is True
        assert corpo["cupom"]["loja"] == "PADARIA ARTESANAL"
        assert len(corpo["cupom"]["itens"]) == 1
        assert corpo["cupom"]["itens"][0]["descricao"] == "BROA DE MILHO"
        assert corpo["cupom"]["valor_pago"] == "7.00"
    config.cache_clear()

