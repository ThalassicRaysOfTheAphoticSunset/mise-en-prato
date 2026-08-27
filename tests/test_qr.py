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


def test_qr_sem_zona_de_silencio():
    """QR colado na borda da imagem, sem a margem branca que a norma pede.

    MEDIDO: o zbar lê isto sem reclamar, então a falta de margem NÃO é motivo
    de falha — a suspeita foi levantada e descartada. O teste fica como
    regressão barata, e as tentativas "com margem" da escada ficam como seguro
    para o caso de fundo escuro colado no código, não como o conserto de nada.
    """
    codigo = qrcode.QRCode(box_size=8, border=0)
    codigo.add_data(URL_QR)
    imagem = codigo.make_image(fill_color="black", back_color="white").convert("RGB")
    assert ler_qr(_bytes(imagem)) == [URL_QR]


def test_qr_sem_margem_e_com_pouco_contraste():
    """Sem margem e cinza sobre cinza — o cupom térmico fotografado de perto."""
    codigo = qrcode.QRCode(box_size=8, border=0)
    codigo.add_data(URL_QR)
    imagem = codigo.make_image(fill_color="black", back_color="white").convert("L")
    assert ler_qr(_bytes(imagem.point(lambda p: 100 + p // 3))) == [URL_QR]


def _chapiscado(escala: int = 8, sigma: int = 90) -> Image.Image:
    """Reproduz o modo de falha vivido: resolução demais + chapisco fino.

    Um iPhone entrega 4032x3024 e o QR da NFC-e tem ~45 módulos, o que dá uns
    40 pixels por módulo. A impressão térmica é chapiscada — o módulo não é um
    quadrado sólido, é uma nuvem de pontos com falhas. Nessa escala o zbar mede
    o chapisco e desiste. Aqui: amplia com NEAREST (módulo grande e duro) e
    mistura ruído fino por cima.
    """
    base = _qr(caixa=6).convert("L")
    grande = base.resize((base.width * escala, base.height * escala), Image.NEAREST)
    return Image.blend(grande, Image.effect_noise(grande.size, sigma), 0.45)


def test_o_modo_de_falha_realmente_derruba_o_zbar_cru():
    """Guarda contra teste vazio: se o zbar cru passar, o teste abaixo não prova nada.

    Foi o erro cometido com a hipótese da zona de silêncio — um teste que
    passaria de qualquer jeito.
    """
    from pyzbar.pyzbar import ZBarSymbol, decode
    assert decode(_chapiscado(), symbols=[ZBarSymbol.QRCODE]) == []


def test_foto_de_celular_com_chapisco_de_impressao_termica():
    """E a escada tem de achar assim mesmo, reduzindo a escala."""
    assert ler_qr(_bytes(_chapiscado().convert("RGB"))) == [URL_QR]


@pytest.mark.parametrize("escala,sigma", [(4, 60), (6, 90), (8, 120)])
def test_chapisco_em_varias_intensidades(escala, sigma):
    assert ler_qr(_bytes(_chapiscado(escala, sigma).convert("RGB"))) == [URL_QR]


# ── os dois decodificadores ──────────────────────────────────────────────────
def _so(nomes: set[str]):
    """A escada com só alguns decodificadores ligados, para isolar cada um."""
    import cupomzinho.qr as modulo

    return [d for d in modulo.DECODIFICADORES if d[0] in nomes]


def test_o_zxing_le_sozinho(monkeypatch):
    """A segunda opinião é opinião de verdade, não enfeite.

    Não há teste aqui provando que ele lê MELHOR: umas 200 degradações
    sintéticas não acharam um caso em que o zbar falhe e o zxing salve. A
    diferença apareceu na foto real de 19/08/2026, que não entra no repo porque
    tem a chave de acesso legível no papel. O que dá para travar é isto: o
    caminho do zxing funciona de ponta a ponta.
    """
    import cupomzinho.qr as modulo

    monkeypatch.setattr(modulo, "DECODIFICADORES", _so({"zxing"}))
    assert ler_qr(_bytes(_qr())) == [URL_QR]


def test_sem_o_zxing_a_escada_continua_de_pé(monkeypatch):
    """A wheel pode faltar (o import é guardado) e o app não pode cair por isso."""
    import cupomzinho.qr as modulo

    monkeypatch.setattr(modulo, "DECODIFICADORES", _so({"zbar"}))
    assert ler_qr(_bytes(_qr())) == [URL_QR]
