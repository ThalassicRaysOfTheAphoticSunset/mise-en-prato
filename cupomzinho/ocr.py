#!/usr/bin/env python3
"""OCR local de cupons fiscais e não fiscais impressos.

Para estabelecimentos que não emitem NFC-e com QR Code (feiras, pequenas padarias,
comércios locais) ou cupons térmicos cujo QR Code ficou ilegível.

Todo o processamento roda 100% no servidor local (casa-srv), sem enviar
imagens nem texto para nenhuma API externa ou nuvem.
"""
import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

import pytesseract
from PIL import Image, ImageFilter, ImageOps
from pytesseract import Output

from .banco import centavos
from .nfce_pr import Cupom, Estabelecimento, Item, normalizar, validar_chave

log = logging.getLogger("cupomzinho.ocr")


def pre_processar_imagem(imagem: Image.Image) -> Image.Image:
    """Prepara a foto para o OCR: orientação EXIF, escala equilibrada e contraste.

    Reduz imagens gigantescas de smartphone para a faixa ideal do Tesseract
    (~1200 a 1600px de lado maior), aplicando desfoque suave antes da redução
    para que a matriz de pontos da impressora térmica não gere serrilhado ruidoso.
    """
    # 1. Rotação correta pelo sensor da câmera do celular
    base = ImageOps.exif_transpose(imagem).convert("L")

    # 2. Correção de orientação se a foto estiver deitada/invertida (apenas com alta confiança)
    try:
        osd = pytesseract.image_to_osd(base, output_type=Output.DICT)
        rotacao = osd.get("rotate", 0)
        conf = float(osd.get("orientation_conf", 0))
        if rotacao in (90, 180, 270) and conf >= 25.0:
            base = base.rotate(360 - rotacao, expand=True)
    except Exception:
        pass

    # 3. Normalização de escala
    lado_maior = max(base.size)
    if lado_maior > 1600:
        fator = 1400 / lado_maior
        suave = base.filter(ImageFilter.GaussianBlur(0.8))
        base = suave.resize(
            (round(base.width * fator), round(base.height * fator)),
            Image.LANCZOS,
        )
    elif lado_maior < 600:
        base = base.resize((base.width * 2, base.height * 2), Image.LANCZOS)

    # 4. Esticar contraste: resgata impressão térmica apagada/cinza
    processada = ImageOps.autocontrast(base, cutoff=2)
    return processada


def procurar_chave_nfce(texto: str) -> str | None:
    """Procura no texto extraído uma chave de acesso de NFC-e de 44 dígitos com DV válido."""
    # Blocos de números que somam 44 dígitos (podem ter espaços ou pontuação)
    padrao = re.compile(r"\b(?:\d[ \t.-]*){43}\d\b")
    for casamento in padrao.finditer(texto):
        apenas_digitos = re.sub(r"\D", "", casamento.group(0))
        if len(apenas_digitos) == 44 and validar_chave(apenas_digitos):
            return apenas_digitos
    for casamento in re.finditer(r"\b\d{44}\b", texto):
        if validar_chave(casamento.group(0)):
            return casamento.group(0)
    return None


def extrair_linhas_estruturadas(imagem: Image.Image) -> list[dict]:
    """Extrai palavras com caixas delimitadoras e agrupa horizontalmente na mesma linha.

    Devolve lista de dicionários ordenados do topo ao fim do documento:
    [{ 'texto': '001 PAO FRANCES KG 0,500 X 16,00 8,00', 'top': 104, 'altura': 20 }, ...]
    """
    dados = pytesseract.image_to_data(
        imagem, lang="por", config="--psm 6", output_type=Output.DICT
    )

    palavras = []
    n = len(dados.get("text", []))
    for i in range(n):
        txt = dados["text"][i].strip()
        conf = int(dados["conf"][i]) if dados["conf"][i] != "-1" else 0
        if txt and conf > 25:
            palavras.append({
                "texto": txt,
                "left": dados["left"][i],
                "top": dados["top"][i],
                "width": dados["width"][i],
                "height": dados["height"][i],
            })

    if not palavras:
        return []

    # Agrupar palavras que pertencem à mesma linha visual (Y próximo)
    palavras.sort(key=lambda p: (p["top"], p["left"]))

    linhas: list[list[dict]] = []
    linha_atual: list[dict] = []
    top_atual = -1
    altura_atual = 10

    for p in palavras:
        if not linha_atual:
            linha_atual = [p]
            top_atual = p["top"]
            altura_atual = p["height"]
        else:
            delta_y = abs(p["top"] - top_atual)
            tolerancia = max(altura_atual, p["height"]) * 0.7
            if delta_y <= tolerancia:
                linha_atual.append(p)
                top_atual = int((top_atual + p["top"]) / 2)
            else:
                linhas.append(linha_atual)
                linha_atual = [p]
                top_atual = p["top"]
                altura_atual = p["height"]

    if linha_atual:
        linhas.append(linha_atual)

    resultado = []
    for lin in linhas:
        lin.sort(key=lambda p: p["left"])
        texto_linha = " ".join(p["texto"] for p in lin)
        resultado.append({
            "texto": texto_linha,
            "top": lin[0]["top"],
            "altura": max(p["height"] for p in lin),
        })

    return resultado


def _para_decimal(s: str) -> Decimal | None:
    if not s:
        return None
    limpo = s.replace(".", "").replace(",", ".").strip()
    try:
        return Decimal(limpo)
    except InvalidOperation:
        return None


def _extrair_cnpj(texto: str) -> str | None:
    m = re.search(r"\b(\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2})\b", texto)
    if m:
        return re.sub(r"\D", "", m.group(1))
    return None


def _extrair_data(texto: str) -> datetime | None:
    m = re.search(r"\b(\d{2}/\d{2}/\d{2,4})\b(?:\s+(\d{2}:\d{2}(?::\d{2})?))?", texto)
    if not m:
        return None
    data_str = m.group(1)
    hora_str = m.group(2) or "12:00:00"
    partes = data_str.split("/")
    if len(partes[2]) == 2:
        partes[2] = "20" + partes[2]
    data_formatada = f"{partes[0]}/{partes[1]}/{partes[2]} {hora_str}"
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(data_formatada, fmt)
        except ValueError:
            continue
    return None


def _parse_linha_item(seq: int, texto: str) -> Item | None:
    """Tenta interpretar uma linha como um item de compra."""
    # Normalizar grudamento comum de OCR: "UNX" -> "UN X", "KGX" -> "KG X"
    texto = re.sub(r"\b(UN|KG|G|GR|PC|CX|LT|L)[xX]\b", r"\1 X", texto, flags=re.IGNORECASE)
    texto = re.sub(r"([0-9])\s*[xX*]\s*([0-9])", r"\1 X \2", texto)

    # Padrão clássico com X:
    # "001 PAO FRANCES KG 0,500 X 16,00 8,00" ou "LEITE INTEGRAL 1 UN X 5,50 5,50"
    m = re.search(
        r"^(?:(\d{1,4})\s+)?(.+?)\s+(\d+(?:[.,]\d+)?)\s*(KG|G|GR|UN|PC|CX|LT|L|PT|PCT)?\s*[xX*]\s*(\d+[.,]\d{2})\s+(\d+[.,]\d{2})$",
        texto,
        re.IGNORECASE,
    )
    if m:
        desc = m.group(2).strip()
        qtd = _para_decimal(m.group(3)) or Decimal("1")
        unidade = (m.group(4) or "").upper()
        if not unidade:
            m_un = re.search(r"\b(KG|G|GR|UN|PC|CX|LT|L|PT|PCT)\b\s*$", desc, re.IGNORECASE)
            if m_un:
                unidade = m_un.group(1).upper()
                desc = re.sub(
                    r"\b(KG|G|GR|UN|PC|CX|LT|L|PT|PCT)\b\s*$", "", desc, flags=re.IGNORECASE
                ).strip()
            elif qtd % 1 != 0:
                unidade = "KG"
            else:
                unidade = "UN"

        unitario = _para_decimal(m.group(5)) or Decimal("0")
        total = _para_decimal(m.group(6)) or (qtd * unitario).quantize(Decimal("0.01"))
        return Item(
            seq=seq,
            descricao=desc,
            codigo_loja=m.group(1) or "",
            quantidade=qtd,
            unidade=unidade,
            valor_unitario=unitario,
            valor_total=total,
        )

    # Padrão sem X: "001 ARROZ 5KG 1 UN 25,90" ou "BATATA 0,500 KG 4,50"
    if not re.search(r"\b[xX*]\b", texto):
        m2 = re.search(
            r"^(?:(\d{1,4})\s+)?(.+?)\s+(\d+(?:[.,]\d+)?)\s*(KG|G|GR|UN|PC|CX|LT|L|PT|PCT)?\s+(\d+[.,]\d{2})$",
            texto,
            re.IGNORECASE,
        )
        if m2:
            desc = m2.group(2).strip()
            if len(desc) >= 2 and not desc.isdigit():
                qtd = _para_decimal(m2.group(3)) or Decimal("1")
                unidade = (m2.group(4) or "").upper()
                if not unidade:
                    m_un = re.search(r"\b(KG|G|GR|UN|PC|CX|LT|L|PT|PCT)\b\s*$", desc, re.IGNORECASE)
                    if m_un:
                        unidade = m_un.group(1).upper()
                        desc = re.sub(
                            r"\b(KG|G|GR|UN|PC|CX|LT|L|PT|PCT)\b\s*$", "", desc, flags=re.IGNORECASE
                        ).strip()
                    elif qtd % 1 != 0:
                        unidade = "KG"
                    else:
                        unidade = "UN"

                total = _para_decimal(m2.group(5)) or Decimal("0")
                unitario = (total / qtd).quantize(Decimal("0.01")) if qtd > 0 else total
                return Item(
                    seq=seq,
                    descricao=desc,
                    codigo_loja=m2.group(1) or "",
                    quantidade=qtd,
                    unidade=unidade,
                    valor_unitario=unitario,
                    valor_total=total,
                )

    return None


def interpretar_cupom_ocr(imagem_ou_dados: bytes | Image.Image) -> Cupom:
    """Interpreta um cupom a partir de imagem (bytes ou PIL.Image).

    Aplica pré-processamento, OCR local, extração dos campos e
    conferência matemática exata de centavos.
    """
    if isinstance(imagem_ou_dados, bytes):
        img_bruta = Image.open(BytesIO(imagem_ou_dados))
    else:
        img_bruta = imagem_ou_dados

    imagem = pre_processar_imagem(img_bruta)

    # Extrair texto completo e linhas estruturadas
    texto_completo = pytesseract.image_to_string(imagem, lang="por")
    linhas_estruturadas = extrair_linhas_estruturadas(imagem)
    textos_linhas = [lin["texto"] for lin in linhas_estruturadas] or texto_completo.splitlines()

    # 1. Atalho: se houver chave de 44 dígitos com DV válido, anota
    chave_encontrada = procurar_chave_nfce(texto_completo)

    # 2. CNPJ
    cnpj = _extrair_cnpj(texto_completo) or "00000000000000"

    # 3. Razão / Nome da loja (primeiras linhas com texto significativo)
    razao = "ESTABELECIMENTO LOCAL"
    for lin in textos_linhas[:5]:
        limpa = lin.strip()
        if len(limpa) >= 4 and not re.search(
            r"(CUPOM|EXTRATO|FISCAL|NFC-E|CNPJ|DOCUMENTO|DANFE|HORA|DATA)", limpa, re.IGNORECASE
        ):
            razao = limpa
            break

    # 4. Data de emissão
    emitido_em = _extrair_data(texto_completo)

    # 5. Itens
    itens: list[Item] = []
    seq = 1
    i = 0
    while i < len(textos_linhas):
        linha = textos_linhas[i].strip()
        if not linha:
            i += 1
            continue

        # Ignorar linhas de cabeçalho, data, CNPJ, traços e totais
        if re.search(
            r"(\b\d{2}/\d{2}/\d{2,4}\b|CNPJ|TOTAL|PAGAR|EXTRATO|CUPOM|SUBTOTAL|-{3,}|\={3,})",
            linha,
            re.IGNORECASE,
        ):
            i += 1
            continue

        item = _parse_linha_item(seq, linha)
        if item:
            itens.append(item)
            seq += 1
            i += 1
            continue

        # Suporte a item impresso em 2 linhas (Linha 1: descrição, Linha 2: valores)
        if i + 1 < len(textos_linhas):
            proxima = textos_linhas[i + 1].strip()
            # Só combina se a próxima linha não for por si só um item completo
            if not _parse_linha_item(seq, proxima):
                linha_combinada = f"{linha} {proxima}"
                item_combinado = _parse_linha_item(seq, linha_combinada)
                if item_combinado:
                    itens.append(item_combinado)
                    seq += 1
                    i += 2
                    continue

        i += 1

    # 6. Totais e Pagamento
    subtotal = None
    total = None
    desconto = Decimal("0")
    valor_pago = None
    forma_pagto = ""

    for linha in textos_linhas:
        m_sub = re.search(r"\bSUBTOTAL\b\s*(?:R\$)?\s*(\d+[.,]\d{2})", linha, re.IGNORECASE)
        if m_sub and subtotal is None:
            subtotal = _para_decimal(m_sub.group(1))

        m_tot = re.search(
            r"\b(?:TOTAL|VALOR TOTAL|TOTAL R\$)\b\s*(?:R\$)?\s*(\d+[.,]\d{2})",
            linha,
            re.IGNORECASE,
        )
        if m_tot and total is None:
            total = _para_decimal(m_tot.group(1))

        m_desc = re.search(
            r"\b(?:DESCONTO|DESC)\b\s*(?:R\$)?\s*(\d+[.,]\d{2})", linha, re.IGNORECASE
        )
        if m_desc:
            desc_val = _para_decimal(m_desc.group(1))
            if desc_val:
                desconto = desc_val

        m_pago = re.search(
            r"\b(?:VALOR A PAGAR|A PAGAR|PAGO|PAGTO)\b\s*(?:R\$)?\s*(\d+[.,]\d{2})",
            linha,
            re.IGNORECASE,
        )
        if m_pago and valor_pago is None:
            valor_pago = _para_decimal(m_pago.group(1))

        if not forma_pagto:
            m_forma = re.search(
                r"\b(PIX|DINHEIRO|CARTAO(?:\s+DE\s+(?:CREDITO|DEBITO))?|DEBITO|CREDITO|ALIMENTACAO)\b",
                linha,
                re.IGNORECASE,
            )
            if m_forma:
                forma_pagto = m_forma.group(1).upper()

    soma_itens = sum((it.valor_total for it in itens), Decimal("0"))
    # Se houver desconto, o 'total' fiscal da NFC-e é o valor dos itens antes do desconto
    if desconto > Decimal("0"):
        total_calc = valor_pago + desconto if valor_pago else total
        total_efetivo = subtotal or (soma_itens if itens else total_calc)
        pago_efetivo = valor_pago or (total_efetivo - desconto)
    else:
        total_efetivo = total or (soma_itens if itens else Decimal("0.00"))
        pago_efetivo = valor_pago or total_efetivo

    # 7. Regra dos Centavos
    avisos: list[str] = []
    if not itens:
        avisos.append("Nenhum item reconhecido no cupom por OCR")
    else:
        esperado = soma_itens - desconto
        diferenca = abs(esperado - pago_efetivo)
        if diferenca > Decimal("0.02"):
            avisos.append(
                f"Divergência de centavos: soma dos itens (R$ {soma_itens}) - "
                f"desconto (R$ {desconto}) = R$ {esperado}, mas total pago é R$ {pago_efetivo} "
                f"(diferença de R$ {diferenca})"
            )

    # Chave sintética única e determinística se não for NFC-e oficial
    if chave_encontrada:
        chave = chave_encontrada
    else:
        dt_marca = emitido_em.strftime("%Y%m%d%H%M%S") if emitido_em else "00000000000000"
        corpo = f"{cnpj}:{dt_marca}:{centavos(pago_efetivo)}:{itens[0].descricao if itens else ''}"
        h = hashlib.sha256(corpo.encode("utf-8")).hexdigest()[:40]
        chave = f"FOTO{h}"

    estabelecimento = Estabelecimento(
        cnpj=cnpj,
        razao=normalizar(razao),
        municipio="PARANÁ",
        uf="PR",
    )

    return Cupom(
        chave=chave,
        estabelecimento=estabelecimento,
        itens=itens,
        emitido_em=emitido_em,
        total=total_efetivo,
        desconto=desconto,
        valor_pago=pago_efetivo,
        qtd_itens=len(itens),
        forma_pagamento=forma_pagto or "DIVERSOS",
        avisos=avisos,
    )
