#!/usr/bin/env python3
"""Leitor do cupom fiscal eletrônico (NFC-e) do Paraná.

Recebe o que o QR Code do cupom carrega — uma URL da consulta pública da
SEFAZ-PR — e devolve a compra estruturada: loja, itens, totais e pagamento.
Nada de OCR: a página da SEFAZ é o extrato exato, item a item.

    python3 -m cupomzinho.nfce_pr 'https://www.fazenda.pr.gov.br/nfce/qrcode?p=...'
    python3 -m cupomzinho.nfce_pr tests/fixtures/cupom_lar_2026-08-14.html

`buscar()` fala com a rede; `interpretar()` é pura e recebe o HTML já em mãos.
A separação existe para o teste rodar contra o cupom guardado, sem internet e
sem incomodar o portal do estado.

O HTML da consulta é inválido de três formas que importam, e todas foram vistas
no cupom real que serve de fixture:

  - `id="linhaTotal"` SE REPETE em todos os totais. Por isso os totais são lidos
    pelo texto do <label>, nunca por id — buscar por id devolveria só o primeiro.
  - `id="Item + 1"` tem espaço no meio do id, o que nenhum seletor de id aceita.
    Daí a varredura por prefixo.
  - `<br></br>` espalhado pelo documento.

É por causa disso que aqui se usa lxml, e não o html.parser da stdlib: o
html.parser tropeça nessas construções, o lxml as engole como um navegador.
"""
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import cycle
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

CONSULTA = "https://www.fazenda.pr.gov.br/nfce/qrcode"


class LayoutDesconhecido(Exception):
    """Veio HTML que o parser não reconhece. O cru fica em disco para virar fixture."""


# ── o que sai daqui ──────────────────────────────────────────────────────────
@dataclass
class Estabelecimento:
    cnpj: str
    razao: str
    logradouro: str = ""
    numero: str = ""
    complemento: str = ""
    bairro: str = ""
    municipio: str = ""
    uf: str = ""


@dataclass
class Item:
    seq: int
    descricao: str
    codigo_loja: str          # o "(Código: 456641)" — é interno da loja, NÃO é GTIN
    quantidade: Decimal
    unidade: str
    valor_unitario: Decimal
    valor_total: Decimal
    gtin: str | None = None
    ncm: str | None = None



@dataclass
class Cupom:
    chave: str
    estabelecimento: Estabelecimento
    itens: list[Item] = field(default_factory=list)
    emitido_em: datetime | None = None
    numero: str = ""
    serie: str = ""
    protocolo: str = ""
    total: Decimal | None = None          # "Valor total R$", antes do desconto
    desconto: Decimal = Decimal("0")
    valor_pago: Decimal | None = None     # "Valor a pagar R$"
    qtd_itens: int | None = None
    forma_pagamento: str = ""
    consumidor: str = ""
    avisos: list[str] = field(default_factory=list)

    @property
    def completo(self) -> bool:
        """Cupom sem aviso nenhum e com a aritmética fechando.

        Cupom incompleto não é recusado — é gravado e mandado para a fila de
        revisão. Meia informação vale mais que nenhuma.
        """
        return not self.avisos


# ── ajudantes de texto ───────────────────────────────────────────────────────
def _limpar(texto: str) -> str:
    """Colapsa espaço, tab e quebra de linha num espaço só."""
    return re.sub(r"\s+", " ", texto or "").strip()


def dinheiro(texto: str) -> Decimal | None:
    """"1.234,56" -> Decimal("1234.56"). Devolve None se não houver número."""
    achado = re.search(r"-?[\d.]*\d(?:,\d+)?", texto or "")
    if not achado:
        return None
    return Decimal(achado.group(0).replace(".", "").replace(",", "."))


def normalizar(descricao: str) -> str:
    """Descrição comparável entre o cupom e a API do Menor Preço.

    Maiúsculas, sem acento, sem pontuação, espaço colapsado. O cupom traz
    "ATUM GOM COSTA RALADO NAT. 170G" e a API "ATUM GOM COSTA RALADO NAT 170G";
    depois disto, são a mesma string.

    ATENÇÃO: isto normaliza, não aproxima. Casamento aproximado é proibido no
    projeto — no mesmo cupom convivem "...RALADO NAT 170G" e "...RALADO DEF
    170G", com GTIN diferente e EXATAMENTE o mesmo preço. Um fuzzy match pegaria
    o produto errado sem fazer barulho.
    """
    sem_acento = unicodedata.normalize("NFKD", descricao or "")
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    so_alfanum = re.sub(r"[^A-Za-z0-9 ]", " ", sem_acento.upper())
    return _limpar(so_alfanum)


def chave_da_url(url: str) -> str | None:
    """Extrai a chave de acesso de 44 dígitos do parâmetro `p` do QR Code.

    O `p` é uma lista separada por barra vertical: chave|versão|ambiente|...
    Aceita também a chave crua, com ou sem os espaços com que a página a exibe.
    """
    bruto = url.strip()
    if "?" in bruto or bruto.startswith("http"):
        p = parse_qs(urlparse(bruto).query).get("p", [""])[0]
        bruto = p.split("|")[0]
    so_digitos = re.sub(r"\D", "", bruto)
    return so_digitos if len(so_digitos) == 44 else None


def digito_verificador(chave: str) -> str:
    """O 44º dígito, calculado sobre os 43 primeiros: módulo 11, pesos 2..9."""
    soma = 0
    for peso, digito in zip(cycle(range(2, 10)), reversed(chave[:43]), strict=False):
        soma += peso * int(digito)
    resto = soma % 11
    return "0" if resto in (0, 1) else str(11 - resto)


def validar_chave(chave: str) -> bool:
    """A chave se confere sozinha — e é isso que salva quem digita 44 números.

    Vale a pena checar ANTES de bater no portal: dígito trocado é o erro comum
    de quem copia do papel, e descobrir isso de graça poupa um captcha.
    """
    return len(chave) == 44 and chave.isdigit() and chave[43] == digito_verificador(chave)


def dados_da_chave(chave: str) -> dict:
    """O que a própria chave já conta, sem consultar ninguém.

    Os 44 dígitos são posicionais (layout da NF-e): serve para preencher loja,
    mês e número de um cupom que entrou sem itens, e para conferir se a data
    digitada bate com o mês da compra.
    """
    return {
        "uf": chave[0:2],
        "ano": 2000 + int(chave[2:4]),
        "mes": int(chave[4:6]),
        "cnpj": chave[6:20],
        "modelo": chave[20:22],
        "serie": chave[22:25].lstrip("0") or "0",
        "numero": chave[25:34].lstrip("0") or "0",
        "tipo_emissao": chave[34],
        "codigo": chave[35:43],
        "dv": chave[43],
    }


def cnpj_formatado(cnpj: str) -> str:
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}" if len(
        cnpj) == 14 else cnpj


# ── a leitura ────────────────────────────────────────────────────────────────
def _ler_estabelecimento(sopa, avisos: list[str]) -> Estabelecimento:
    topo = sopa.select_one("#u20")
    razao = _limpar(topo.get_text()) if topo else ""

    cnpj, endereco = "", ""
    for div in sopa.select("div.text"):
        texto = _limpar(div.get_text())
        if "CNPJ" in texto.upper():
            cnpj = re.sub(r"\D", "", texto)
        elif texto and not endereco:
            endereco = texto

    if len(cnpj) != 14:
        avisos.append("CNPJ do emitente não reconhecido")
    if not razao:
        avisos.append("razão social do emitente não reconhecida")

    # "AV RIO GRANDE DO SUL, 2876, , SAO CRISTOVAO, MEDIANEIRA, PR"
    # O complemento vem vazio com frequência, então a posição importa mais que
    # o conteúdo: são sempre seis campos, alguns em branco.
    partes = [_limpar(p) for p in endereco.split(",")] if endereco else []
    partes += [""] * (6 - len(partes))
    return Estabelecimento(
        cnpj=cnpj, razao=razao,
        logradouro=partes[0], numero=partes[1], complemento=partes[2],
        bairro=partes[3], municipio=partes[4], uf=partes[5],
    )


def _campo(tr, classe: str, rotulo: str = "") -> str:
    """Texto de um <span class=...> da linha do item, sem o rótulo em negrito.

    O documento escreve `<span class="Rqtd"><strong>Qtde.:</strong>1</span>`,
    então o valor só aparece depois de tirar o rótulo do meio do texto.
    """
    alvo = tr.select_one(f"span.{classe}")
    if not alvo:
        return ""
    texto = _limpar(alvo.get_text())
    return _limpar(texto.replace(rotulo, "")) if rotulo else texto


def _ler_itens(sopa, avisos: list[str]) -> list[Item]:
    itens: list[Item] = []
    # id="Item + 1" tem espaço: nenhum seletor de id serve, então varre-se as
    # linhas e filtra-se pelo prefixo.
    linhas = [tr for tr in sopa.select("table#tabResult tr")
              if (tr.get("id") or "").startswith("Item")]
    if not linhas:
        avisos.append("nenhum item encontrado na nota")

    for seq, tr in enumerate(linhas, start=1):
        # O DANFE do QR escreve a descrição em txtTit2; a consulta completa
        # (pela chave digitada) serve a MESMA linha com txtTit. Medido em
        # 30/08/2026: fora essa classe, as duas telas são idênticas item a item.
        descricao = _campo(tr, "txtTit2") or _campo(tr, "txtTit")
        codigo = re.sub(r"\D", "", _campo(tr, "RCod"))            # "(Código: 456641)"
        quantidade = dinheiro(_campo(tr, "Rqtd", "Qtde.:"))      # fracionária em item por peso
        unidade = _campo(tr, "RUN", "UN:")
        unitario = dinheiro(_campo(tr, "RvlUnit", "Vl. Unit.:"))
        total = dinheiro(_campo(tr, "valor"))

        if descricao and quantidade is not None and unitario is not None and total is not None:
            itens.append(Item(seq, descricao, codigo, quantidade, unidade, unitario, total))
        else:
            avisos.append(f"item {seq} veio incompleto ({descricao or 'sem descrição'})")
    return itens


def _ler_totais(sopa, cupom: Cupom) -> None:
    """Lê o quadro de totais PELO TEXTO DO RÓTULO.

    Aqui está a armadilha principal do documento: todas estas linhas têm
    id="linhaTotal", repetido. Procurar por id devolveria só a primeira e os
    outros valores viriam errados — em silêncio, que é o pior jeito de errar.
    """
    quadro = sopa.select_one("#totalNota")
    if not quadro:
        cupom.avisos.append("quadro de totais não encontrado")
        return

    rotulos = {
        "qtd. total de itens": "qtd_itens",
        "valor total r$": "total",
        "descontos r$": "desconto",
        "valor a pagar r$": "valor_pago",
    }
    formas: list[tuple[str, Decimal]] = []
    pagamento_comecou = False

    for linha in quadro.find_all("div", recursive=False):
        rotulo = linha.find("label")
        valor = linha.find("span")
        if not rotulo:
            continue
        chave_rotulo = _limpar(rotulo.get_text()).lower().rstrip(":")

        # A partir de "Forma de pagamento:" as linhas deixam de ser totais e
        # passam a ser as formas usadas (pode haver mais de uma na mesma compra).
        if chave_rotulo.startswith("forma de pagamento"):
            pagamento_comecou = True
            continue

        if not valor:
            continue
        if pagamento_comecou:
            if "tributos" in chave_rotulo:      # a linha da Lei 12.741 não é pagamento
                continue
            pago = dinheiro(valor.get_text())
            if pago is not None:
                formas.append((_limpar(rotulo.get_text()), pago))
        elif chave_rotulo in rotulos:
            campo = rotulos[chave_rotulo]
            if campo == "qtd_itens":
                cupom.qtd_itens = int(dinheiro(valor.get_text()) or 0)
            else:
                setattr(cupom, campo, dinheiro(valor.get_text()) or Decimal("0"))

    cupom.forma_pagamento = " + ".join(nome for nome, _ in formas)


def _ler_infos(sopa, cupom: Cupom) -> None:
    """Número, série, emissão, protocolo e consumidor — texto corrido em #infos."""
    bloco = sopa.select_one("#infos")
    texto = _limpar(bloco.get_text()) if bloco else ""

    def pegar(padrao: str) -> str:
        achado = re.search(padrao, texto)
        return _limpar(achado.group(1)) if achado else ""

    cupom.numero = pegar(r"Número:\s*(\d+)")
    cupom.serie = pegar(r"Série:\s*(\d+)")
    cupom.protocolo = pegar(r"Protocolo de Autorização:\s*(\d+)")

    emissao = pegar(r"Emissão:\s*(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})")
    if emissao:
        cupom.emitido_em = datetime.strptime(emissao, "%d/%m/%Y %H:%M:%S")
    else:
        cupom.avisos.append("data de emissão não reconhecida")

    cpf = re.search(r"CPF:?\s*([\d.\-]{11,14})", texto)
    cupom.consumidor = re.sub(r"\D", "", cpf.group(1)) if cpf else "não identificado"

    # O rodapé traz "Operador(a): <matrícula>-<NOME COMPLETO DO CAIXA>". É dado
    # pessoal de um funcionário da loja e não tem serventia nenhuma para o
    # controle de gasto da casa, então NÃO é lido nem guardado. De propósito.


def _detectar_layout(sopa) -> str:
    if sopa.select_one("#tabResult") or sopa.select_one("#u20"):
        return "danfe_mobile"
    if (
        sopa.select_one(".tabela_produtos")
        or sopa.select_one("#tabProdutos")
        or sopa.select_one("#NFe")
        or sopa.select_one("#GeralXslt")
        or any("ncm" in th.get_text().lower() for th in sopa.select("th"))
    ):
        return "sped_completo"
    return "desconhecido"


def _ler_sped_completo(sopa, chave: str, avisos: list[str]) -> Cupom:
    from .sanidade import digito_gtin_confere

    def valor_por_label(contexto, texto_label: str) -> str:
        alvo = texto_label.lower()
        for rotulo in contexto.select("label"):
            if alvo in _limpar(rotulo.get_text()).lower():
                pai = rotulo.parent
                if pai:
                    span = pai.select_one("span")
                    if span and span != rotulo:
                        return _limpar(span.get_text())
                    texto = _limpar(pai.get_text().replace(rotulo.get_text(), ""))
                    if texto:
                        return texto
        return ""

    bloco_emit = sopa.select_one("fieldset.bloco_emitente") or sopa
    razao = (
        _limpar(sopa.select_one("span.razao_social").get_text())
        if sopa.select_one("span.razao_social")
        else valor_por_label(bloco_emit, "Razão Social")
    )
    cnpj_bruto = (
        _limpar(sopa.select_one("span.cnpj").get_text())
        if sopa.select_one("span.cnpj")
        else valor_por_label(bloco_emit, "CNPJ")
    )
    cnpj = re.sub(r"\D", "", cnpj_bruto)
    if len(cnpj) != 14 and chave and len(chave) == 44:
        cnpj = chave[6:20]

    endereco_bruto = (
        _limpar(sopa.select_one("span.endereco").get_text())
        if sopa.select_one("span.endereco")
        else valor_por_label(bloco_emit, "Endereço")
    )
    partes = [_limpar(p) for p in endereco_bruto.split(",")] if endereco_bruto else []
    partes += [""] * (6 - len(partes))
    estab = Estabelecimento(
        cnpj=cnpj, razao=razao,
        logradouro=partes[0], numero=partes[1], complemento=partes[2],
        bairro=partes[3], municipio=partes[4], uf=partes[5],
    )
    if not estab.razao:
        avisos.append("razão social do emitente não reconhecida")
    if len(estab.cnpj) != 14:
        avisos.append("CNPJ do emitente não reconhecido")

    bloco_id = sopa.select_one("fieldset.bloco_identificacao") or sopa
    numero = valor_por_label(bloco_id, "Número")
    serie = valor_por_label(bloco_id, "Série")
    protocolo = valor_por_label(bloco_id, "Protocolo")

    emissao_texto = valor_por_label(bloco_id, "Emissão") or valor_por_label(bloco_id, "Data")
    emitido_em = None
    if emissao_texto:
        achado = re.search(r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})", emissao_texto)
        if achado:
            emitido_em = datetime.strptime(achado.group(1), "%d/%m/%Y %H:%M:%S")
    if not emitido_em and chave and len(chave) == 44:
        d = dados_da_chave(chave)
        numero = numero or d["numero"]
        serie = serie or d["serie"]

    if not emitido_em:
        avisos.append("data de emissão não reconhecida")

    itens: list[Item] = []
    tabela = sopa.select_one("table.tabela_produtos") or sopa.select_one("table#tabProdutos")
    if not tabela:
        for tab in sopa.select("table"):
            ths = [normalizar(th.get_text()).lower() for th in tab.select("th")]
            if any("desc" in t or "prod" in t for t in ths) and any(
                "qtd" in t or "quant" in t for t in ths
            ):
                tabela = tab
                break

    if tabela:
        ths = tabela.select("thead th") or tabela.select("tr th")
        col_map = {}
        for idx, th in enumerate(ths):
            txt = normalizar(th.get_text()).lower()
            if any(k in txt for k in ("unit", "vun")):
                col_map["unitario"] = idx
            elif any(k in txt for k in ("total", "vtot", "vprod")):
                col_map["total"] = idx
            elif any(k in txt for k in ("qtd", "quant")):
                col_map["quantidade"] = idx
            elif "ean" in txt or "gtin" in txt or "barras" in txt:
                col_map["gtin"] = idx
            elif "ncm" in txt:
                col_map["ncm"] = idx
            elif "codigo" in txt or "cprod" in txt:
                col_map["codigo"] = idx
            elif "unid" in txt or re.search(r"\b(un|ucom|utrib)\b", txt):
                col_map["unidade"] = idx
            elif any(k in txt for k in ("desc", "produto", "servico")):
                col_map["descricao"] = idx
            elif any(k in txt for k in ("item", "num", "seq")):
                col_map["seq"] = idx

        def valor_celula(tds, chave_col, padrao=""):
            if chave_col in col_map and col_map[chave_col] < len(tds):
                return _limpar(tds[col_map[chave_col]].get_text())
            return padrao

        linhas = tabela.select("tbody tr") or [tr for tr in tabela.select("tr") if tr.select("td")]
        for seq_idx, tr in enumerate(linhas, start=1):
            tds = tr.select("td")
            if not tds:
                continue

            descricao = valor_celula(tds, "descricao")
            if not descricao:
                continue

            quantidade = dinheiro(valor_celula(tds, "quantidade"))
            unidade = valor_celula(tds, "unidade")
            unitario = dinheiro(valor_celula(tds, "unitario"))
            total = dinheiro(valor_celula(tds, "total"))
            codigo = re.sub(r"\D", "", valor_celula(tds, "codigo"))

            gtin_bruto = valor_celula(tds, "gtin")
            gtin = None
            if gtin_bruto and gtin_bruto.upper() != "SEM GTIN" and gtin_bruto != "0":
                so_dig = re.sub(r"\D", "", gtin_bruto)
                if digito_gtin_confere(so_dig):
                    gtin = so_dig

            ncm_bruto = valor_celula(tds, "ncm")
            so_ncm = re.sub(r"\D", "", ncm_bruto)
            ncm = so_ncm if len(so_ncm) == 8 else None

            if quantidade is not None and unitario is not None and total is not None:
                itens.append(
                    Item(
                        seq=seq_idx,
                        descricao=descricao,
                        codigo_loja=codigo,
                        quantidade=quantidade,
                        unidade=unidade,
                        valor_unitario=unitario,
                        valor_total=total,
                        gtin=gtin,
                        ncm=ncm,
                    )
                )
            else:
                avisos.append(f"item {seq_idx} veio incompleto ({descricao})")

    if not itens:
        avisos.append("nenhum item encontrado na nota")

    bloco_tot = sopa.select_one("fieldset.bloco_totais") or sopa
    total_prod = dinheiro(
        _limpar(sopa.select_one("span.total_produtos").get_text())
        if sopa.select_one("span.total_produtos")
        else valor_por_label(bloco_tot, "Total dos Produtos")
    )
    total_nota = dinheiro(
        _limpar(sopa.select_one("span.total_nota").get_text())
        if sopa.select_one("span.total_nota")
        else (
            valor_por_label(bloco_tot, "Total da NFC-e")
            or valor_por_label(bloco_tot, "Valor Total")
        )
    )
    desconto = (
        dinheiro(
            _limpar(sopa.select_one("span.desconto").get_text())
            if sopa.select_one("span.desconto")
            else valor_por_label(bloco_tot, "Desconto")
        )
        or Decimal("0")
    )

    forma_pgto = (
        _limpar(sopa.select_one("span.forma_pagamento").get_text())
        if sopa.select_one("span.forma_pagamento")
        else valor_por_label(bloco_tot, "Forma de Pagamento")
    )

    total_final = total_nota or total_prod
    valor_pago = total_nota or (total_prod - desconto if total_prod is not None else None)

    return Cupom(
        chave=chave,
        estabelecimento=estab,
        itens=itens,
        emitido_em=emitido_em,
        numero=numero,
        serie=serie,
        protocolo=protocolo,
        total=total_final,
        desconto=desconto,
        valor_pago=valor_pago,
        qtd_itens=len(itens),
        forma_pagamento=forma_pgto,
        consumidor="não identificado",
        avisos=avisos,
    )


def interpretar(html: str) -> Cupom:
    """HTML da consulta pública (DANFE QR ou SPED Completo) -> Cupom. Pura: não toca a rede."""
    sopa = BeautifulSoup(html, "lxml")
    avisos: list[str] = []

    span_chave = sopa.select_one("span.chave")
    chave = re.sub(r"\D", "", span_chave.get_text()) if span_chave else ""
    if not chave:
        achado = re.search(r"\b(\d{44})\b", re.sub(r"\s+", "", sopa.get_text()))
        if achado:
            chave = achado.group(1)

    if len(chave) != 44:
        avisos.append("chave de acesso não reconhecida")

    layout = _detectar_layout(sopa)
    if layout == "sped_completo":
        cupom = _ler_sped_completo(sopa, chave, avisos)
    else:
        # Padrão ou DANFE mobile: tenta ler campos e acumula avisos se estiver vazio
        cupom = Cupom(
            chave=chave, estabelecimento=_ler_estabelecimento(sopa, avisos), avisos=avisos
        )
        cupom.itens = _ler_itens(sopa, avisos)
        _ler_totais(sopa, cupom)
        _ler_infos(sopa, cupom)

    # Conferência de caixa: soma dos itens - desconto tem de bater com o pago.
    if cupom.itens and cupom.valor_pago is not None:
        somado = sum(i.valor_total for i in cupom.itens) - cupom.desconto
        if abs(somado - cupom.valor_pago) > Decimal("0.01"):
            cupom.avisos.append(
                f"soma dos itens ({somado}) não bate com o valor pago ({cupom.valor_pago})"
            )
    return cupom


def buscar(url_ou_chave: str, tempo_limite: int = 30) -> str:
    """Baixa o HTML da consulta pública. Precisa da URL inteira do QR Code.

    A chave sozinha não basta: a página exige o `p` completo, com o hash de
    segurança que o emitente calculou. Guardar a URL do QR é, portanto,
    obrigatório para conseguir reprocessar depois.
    """
    import httpx

    url = url_ou_chave if url_ou_chave.startswith("http") else f"{CONSULTA}?p={url_ou_chave}"
    resposta = httpx.get(url, timeout=tempo_limite, follow_redirects=True)
    resposta.raise_for_status()
    return resposta.text


def _main(argumentos: list[str]) -> int:
    if not argumentos:
        print(__doc__)
        return 2
    alvo = argumentos[0]
    html = Path(alvo).read_text(encoding="utf-8") if Path(alvo).exists() else buscar(alvo)
    cupom = interpretar(html)
    e = cupom.estabelecimento

    print(f"{e.razao}  (CNPJ {e.cnpj})")
    print(f"{e.logradouro}, {e.numero} — {e.bairro}, {e.municipio}/{e.uf}")
    print(f"emitido em {cupom.emitido_em}   nº {cupom.numero}/{cupom.serie}")
    print(f"chave {cupom.chave}\n")
    for i in cupom.itens:
        print(f"  {i.seq:>3}. {i.descricao[:44]:<46} {i.quantidade:>7} {i.unidade:<4}"
              f" x {i.valor_unitario:>8} = {i.valor_total:>9}")
    print(f"\n  {'itens:':<16}{cupom.qtd_itens}")
    print(f"  {'total:':<16}{cupom.total}")
    print(f"  {'desconto:':<16}{cupom.desconto}")
    print(f"  {'a pagar:':<16}{cupom.valor_pago}   ({cupom.forma_pagamento})")
    print(f"  {'consumidor:':<16}{cupom.consumidor}")
    if cupom.avisos:
        print("\n  avisos:")
        for aviso in cupom.avisos:
            print(f"    ! {aviso}")
    else:
        print("\n  cupom completo, aritmética fechada")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
