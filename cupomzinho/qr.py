#!/usr/bin/env python3
"""Achar o QR Code numa foto de celular.

Quem decodifica é o servidor, e não o navegador, porque o Safari do iPhone não
tem `BarcodeDetector` — depender dele deixaria metade da casa de fora.

O pyzbar sozinho é exigente demais para foto de cupom no mundo real: o papel é
térmico e de baixo contraste, a foto sai de 4000 px torta pelo EXIF, e o QR
costuma ocupar um pedaço pequeno do quadro. Então aqui não há uma tentativa, há
uma escada de tentativas, da mais barata para a mais cara, parando na primeira
que achar alguma coisa.
"""
import logging
from io import BytesIO

from PIL import Image, ImageOps
from pyzbar.pyzbar import ZBarSymbol, decode

log = logging.getLogger("cupomzinho.qr")

# Acima disto o zbar fica lento sem ficar melhor; abaixo, o QR some no ruído.
LADO_BOM = 1600

# A norma pede 4 módulos de branco em volta do código. MEDIDO: o zbar lê sem
# essa margem numa imagem limpa, então a falta dela não é, sozinha, motivo de
# falha. As tentativas "com margem" ficam como seguro barato para o caso de o
# código encostar num fundo escuro — não como conserto de um defeito conhecido.
MARGEM = 40


def _com_margem(imagem: Image.Image) -> Image.Image:
    """Devolve a zona de silêncio que o enquadramento comeu."""
    return ImageOps.expand(imagem, border=MARGEM, fill=255)


def _tentativas(imagem: Image.Image):
    """As variações da imagem, em ordem de custo. Cada uma é um palpite sobre
    por que a anterior falhou."""
    # 1. como veio, só endireitada e em cinza — resolve a maioria
    base = ImageOps.exif_transpose(imagem).convert("L")
    yield "cinza", base

    # 2. foto grande demais: reduzir tira o ruído do sensor e o zbar respira
    maior = max(base.size)
    if maior > LADO_BOM:
        fator = LADO_BOM / maior
        yield "reduzida", base.resize(
            (int(base.width * fator), int(base.height * fator)), Image.LANCZOS)

    # 3. cupom térmico é cinza sobre cinza: esticar o contraste separa o código
    yield "contraste", ImageOps.autocontrast(base, cutoff=2)

    # 4. QR pequeno no quadro: ampliar dá pixels para o zbar medir os módulos
    if maior < LADO_BOM * 2:
        yield "ampliada", base.resize((base.width * 2, base.height * 2), Image.LANCZOS)

    # 5. preto no branco, sem meio-termo
    binarizada = ImageOps.autocontrast(base, cutoff=2).point(lambda p: 255 if p > 128 else 0)
    yield "binarizada", binarizada

    # 6. as mesmas, agora com a zona de silêncio devolvida. Vem por último não
    # por ser improvável — é o caso de quem mira bem —, mas porque as anteriores
    # são mais baratas e cobrem a foto folgada, que é a maioria.
    yield "com margem", _com_margem(base)
    yield "com margem e contraste", _com_margem(ImageOps.autocontrast(base, cutoff=2))
    yield "com margem, ampliada", _com_margem(
        base.resize((base.width * 2, base.height * 2), Image.LANCZOS))
    yield "com margem, binarizada", _com_margem(binarizada)


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
