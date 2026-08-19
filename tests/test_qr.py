"""A escada de tentativas para achar o QR numa foto de celular.

Os casos são degradações de propósito do que uma foto de cupom sofre no mundo
real: QR pequeno num quadro grande, papel térmico sem contraste, foto torta.
"""
from io import BytesIO

import pytest
import qrcode
from PIL import Image, ImageFilter

from cupomzinho.qr import ler_qr

URL_QR = ("https://www.fazenda.pr.gov.br/nfce/qrcode?p=41260877752293016191651170001637541131086428"
          "|2|1|1|1E778A47C5BAEDD69BFCD4A14F8CD2A19C5CD919")


def _qr(conteudo: str = URL_QR, caixa: int = 8) -> Image.Image:
    codigo = qrcode.QRCode(box_size=caixa, border=4)
    codigo.add_data(conteudo)
    return codigo.make_image(fill_color="black", back_color="white").convert("RGB")


def _bytes(imagem: Image.Image, formato: str = "JPEG", qualidade: int = 92) -> bytes:
    buffer = BytesIO()
    imagem.save(buffer, formato, quality=qualidade)
    return buffer.getvalue()


def test_qr_limpo():
    assert ler_qr(_bytes(_qr())) == [URL_QR]


def test_qr_pequeno_num_quadro_grande():
    """O caso normal: você fotografa o cupom inteiro e o QR é um pedacinho."""
    fundo = Image.new("RGB", (2400, 3200), "white")
    codigo = _qr(caixa=6)
    fundo.paste(codigo, (900, 2200))
    assert ler_qr(_bytes(fundo)) == [URL_QR]


def test_cupom_termico_sem_contraste():
    """Papel térmico velho: cinza sobre cinza, não preto sobre branco."""
    codigo = _qr().point(lambda p: 110 + p // 4)     # esmaga a faixa de tons
    assert ler_qr(_bytes(codigo)) == [URL_QR]


def test_foto_levemente_desfocada():
    codigo = _qr(caixa=10).filter(ImageFilter.GaussianBlur(radius=1.4))
    assert ler_qr(_bytes(codigo)) == [URL_QR]


def test_jpeg_bem_comprimido():
    """Celular economizando banda no upload."""
    assert ler_qr(_bytes(_qr(caixa=10), qualidade=35)) == [URL_QR]


def test_imagem_sem_qr_nenhum():
    assert ler_qr(_bytes(Image.new("RGB", (800, 600), "white"))) == []


def test_bytes_que_nao_sao_imagem():
    with pytest.raises(ValueError, match="não consegui abrir a imagem"):
        ler_qr(b"isto nao e uma foto")


def test_png_tambem_serve():
    assert ler_qr(_bytes(_qr(), "PNG")) == [URL_QR]
