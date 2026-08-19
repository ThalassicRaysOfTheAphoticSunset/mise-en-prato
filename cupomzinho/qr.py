#!/usr/bin/env python3
"""Achar o QR Code numa foto de celular.

Quem decodifica é o servidor, e não o navegador, porque o Safari do iPhone não
tem `BarcodeDetector` — depender dele deixaria metade da casa de fora.

O pyzbar sozinho não dá conta de foto de cupom no mundo real, e o motivo é o
contrário do que a intuição diz: **sobra resolução**. Um iPhone entrega
4032x3024; o QR da NFC-e ocupa boa parte do quadro e tem ~45 módulos de lado,
o que dá uns 40 pixels POR MÓDULO. Só que a impressão térmica é chapiscada —
o módulo não é um quadrado sólido, é uma nuvem de pontos com falhas. Nessa
escala o zbar mede o chapisco em vez do módulo, e desiste.

Medido contra a foto real que falhou (3024x4032, cupom térmico gasto):

    reduzida para 1600, 1200, 900, 700 ......... falhou
    reduzida para 500, 400, 300 ................ achou
    desfoque 4 + reduzida para 1200, 800, 600 .. achou
    mediana, em qualquer tamanho grande ........ falhou

Ou seja: o conserto é reduzir, e desfocar ANTES de reduzir amplia muito a
margem — o desfoque dissolve o chapisco antes que a redução o transforme em
ruído. É o que a escada abaixo faz, varrendo escalas da maior para a menor.
"""
import logging
from io import BytesIO

from PIL import Image, ImageFilter, ImageOps
from pyzbar.pyzbar import ZBarSymbol, decode

log = logging.getLogger("cupomzinho.qr")

# A varredura de escalas, em pixels do lado maior. Começa perto do original
# (foto já boa passa na primeira) e desce até o tamanho em que um QR de 45
# módulos ainda tem uns 7 pixels por módulo — abaixo disso não há o que ler.
ESCALAS = (1600, 1100, 800, 600, 450, 320)

# Margem branca em volta, para o caso de o código encostar num fundo escuro.
# MEDIDO: o zbar lê QR sem zona de silêncio numa imagem limpa, então isto é
# seguro barato, não conserto de defeito conhecido.
MARGEM = 40


def _reduzir(imagem: Image.Image, lado: int) -> Image.Image | None:
    """Reduz para `lado`, desfocando na medida da redução.

    O raio cresce junto com o fator: reduzir 4x sem filtrar antes é o mesmo que
    amostrar o chapisco em vez da média dele. É anti-serrilhado com um pouco de
    folga a mais, de propósito.
    """
    fator = lado / max(imagem.size)
    if fator >= 1:
        return None
    raio = max(1.0, 0.9 / fator)
    suave = imagem.filter(ImageFilter.GaussianBlur(raio))
    return suave.resize(
        (max(1, round(imagem.width * fator)), max(1, round(imagem.height * fator))),
        Image.LANCZOS)


def _tentativas(imagem: Image.Image):
    """As variações, da mais barata e provável para a mais desesperada."""
    base = ImageOps.exif_transpose(imagem).convert("L")

    # 1. como veio. Foto já pequena, ou captura de tela, passa aqui.
    yield "cinza", base

    # 2. a varredura de escalas — é esta que resolve foto de celular.
    reduzidas = []
    for lado in ESCALAS:
        menor = _reduzir(base, lado)
        if menor is not None:
            reduzidas.append((lado, menor))
            yield f"reduzida {lado}", menor

    # 3. contraste esticado: cupom térmico velho é cinza sobre cinza.
    for lado, menor in reduzidas[:3] or [(0, base)]:
        yield f"contraste {lado}", ImageOps.autocontrast(menor, cutoff=2)

    # 4. preto no branco, sem meio-termo.
    for lado, menor in reduzidas[:3] or [(0, base)]:
        yield f"binarizada {lado}", ImageOps.autocontrast(menor, cutoff=2).point(
            lambda p: 255 if p > 128 else 0)

    # 5. ampliar, para o caso oposto: QR pequenininho num quadro grande.
    if max(base.size) < 1200:
        yield "ampliada", base.resize((base.width * 2, base.height * 2), Image.LANCZOS)

    # 6. com a margem branca devolvida.
    alvo = reduzidas[2][1] if len(reduzidas) > 2 else base
    yield "com margem", ImageOps.expand(alvo, border=MARGEM, fill=255)


def ler_qr(dados: bytes) -> list[str]:
    """Todo conteúdo de QR Code achado na imagem. Lista vazia se não achou nada.

    Levanta ValueError se os bytes não forem uma imagem que o Pillow abra.
    """
    try:
        imagem = Image.open(BytesIO(dados))
        imagem.load()
    except Exception as erro:
        raise ValueError(f"não consegui abrir a imagem: {erro}") from erro

    log.info("foto recebida: %s %sx%s, %d KB",
             imagem.format, imagem.width, imagem.height, len(dados) // 1024)

    for nome, variante in _tentativas(imagem):
        achados = decode(variante, symbols=[ZBarSymbol.QRCODE])
        if achados:
            log.info("QR achado na tentativa '%s' (%d código(s))", nome, len(achados))
            return [c.data.decode("utf-8", "replace") for c in achados]
        log.debug("tentativa '%s': nada", nome)

    log.info("nenhum QR Code em nenhuma das tentativas")
    return []
