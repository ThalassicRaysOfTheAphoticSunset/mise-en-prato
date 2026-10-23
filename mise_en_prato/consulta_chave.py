#!/usr/bin/env python3
"""A segunda porta do portal: consultar o cupom pela chave de acesso digitada.

Quando o QR está manchado, a rota normal (`nfce_pr.buscar`) não tem como
funcionar, e não é falta de esperteza: o `p` do QR Code carrega um hash SHA-1
que o emitente calcula com o CSC dele, um segredo que não é impresso no papel e
não se deriva da chave. Medido contra o portal em 27/08/2026:

    p=<chave>|2|1|1|<hash certo> .... 200, o DANFE inteiro
    p=<chave> ....................... 200, "Erro interno no servidor"
    p=<chave>|2|1|1|<hash zerado> ... 200, "100 - QR Code Inválido"

Só que o estado mantém uma segunda consulta, a **Consulta Completa NFC-e**, e
essa aceita a chave digitada — cobrando um captcha de imagem. É o que este
módulo faz: busca o formulário, traz a imagem do captcha até a tela do morador
e devolve ao portal a resposta que ELE digitou.

O captcha continua sendo resolvido por gente. Não há OCR, não há laço, não há
agendamento: um cupom por vez, quando alguém pede — a mesma disciplina que o
cliente do Menor Preço segue por causa do termo de uso da CELEPAR. O que muda é
só a tela em que a pessoa digita as letras.
"""
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .menor_preco import UA
from .nfce_pr import Cupom, interpretar

log = logging.getLogger("mise_en_prato.consulta_chave")

# O formulário, no endereço direto. A rota amigável
# (www.fazenda.pr.gov.br/nfce/consulta) responde 301 para "www.sped..." — e ESSE
# host serve um certificado auto-assinado, vencido, emitido para
# www.drupalweb.celepar.pr.gov.br. Medido em 27/08/2026.
#
# Desligar a verificação de TLS para contornar seria trocar um defeito do
# portal por um defeito nosso, então o caminho é entrar direto no host que tem
# certificado válido — o mesmo para onde o `action` do formulário aponta.
FORMULARIO = "https://sped.fazenda.pr.gov.br/NFCe/webservices/sped/nfce/completa"

# Quanto tempo um desafio de captcha vale. O do Drupal dura mais, mas guardar
# sessão do estado por muito tempo não serve a ninguém.
VALIDADE = timedelta(minutes=5)


class PortalMudou(Exception):
    """O formulário não veio como esperado — campo faltando, imagem sumida."""


class CaptchaRecusado(Exception):
    """As letras não conferiram. Não é erro do morador nem do código: acontece."""


class ChaveNaoAchada(Exception):
    """O portal respondeu, mas não tem essa NFC-e (chave errada, ou nota nova demais)."""


class LayoutDesconhecido(Exception):
    """Veio HTML que o parser não reconhece. O cru fica em disco para virar fixture."""


@dataclass
class Desafio:
    """Um captcha aberto, esperando as letras. Vive em memória, nunca em disco."""

    id: str
    url: str                       # onde o POST vai
    campos: dict[str, str]         # os ocultos do Drupal, repetidos de volta
    cookies: dict[str, str]
    imagem: bytes                  # os bytes da imagem do captcha
    # MEDIDO: o portal serve JPEG, apesar do nome "image-captcha-generate". O
    # tipo vem do cabeçalho e não de palpite — data URI com tipo errado é o
    # tipo de coisa que funciona em um navegador e falha em outro.
    tipo: str = "image/jpeg"
    criado_em: datetime = field(default_factory=datetime.now)

    @property
    def vencido(self) -> bool:
        return datetime.now() - self.criado_em > VALIDADE

    @property
    def imagem_data_uri(self) -> str:
        import base64

        return f"data:{self.tipo};base64," + base64.b64encode(self.imagem).decode()


def _cliente(tempo_limite: int, cookies: dict | None = None):
    import httpx

    return httpx.Client(
        timeout=tempo_limite, follow_redirects=True, cookies=cookies or {},
        headers={"User-Agent": UA, "Accept-Language": "pt-BR"})


def abrir_desafio(tempo_limite: int = 30) -> Desafio:
    """Pega um captcha novo no portal, com os campos ocultos que o acompanham."""
    with _cliente(tempo_limite) as cliente:
        pagina = cliente.get(FORMULARIO)
        pagina.raise_for_status()

        sopa = BeautifulSoup(pagina.text, "lxml")
        formulario = sopa.select_one("form#sefa-sped-form")
        if not formulario:
            raise PortalMudou("não achei o formulário da consulta completa")

        campos = {
            entrada["name"]: entrada.get("value", "")
            for entrada in formulario.select("input[type=hidden][name]")
        }
        # O botão do Drupal também é campo: sem o `op` o formulário não submete.
        campos["op"] = "Consultar"
        # 1 = produção, 2 = homologação. Cupom de mercado é sempre produção.
        campos["txAmbiente"] = "1"
        faltando = {"form_build_id", "form_id", "captcha_sid", "captcha_token"} - set(campos)
        if faltando:
            raise PortalMudou(f"o formulário veio sem {', '.join(sorted(faltando))}")

        imagem_tag = formulario.select_one("img[src*='captcha']")
        if not imagem_tag:
            raise PortalMudou("o formulário veio sem imagem de captcha")
        figura = cliente.get(urljoin(str(pagina.url), imagem_tag["src"]))
        figura.raise_for_status()

        desafio = Desafio(
            id=secrets.token_urlsafe(9),
            url=urljoin(str(pagina.url), formulario.get("action", "")),
            campos=campos,
            cookies=dict(cliente.cookies),
            imagem=figura.content,
            tipo=figura.headers.get("content-type", "image/jpeg").split(";")[0].strip(),
        )
    log.info("desafio %s aberto (%d bytes de captcha)", desafio.id, len(desafio.imagem))
    return desafio


def consultar(desafio: Desafio, chave: str, captcha: str, tempo_limite: int = 45) -> str:
    """Manda chave + captcha e devolve o HTML cru da resposta.

    Levanta CaptchaRecusado quando o portal repete o formulário — é o sinal de
    que as letras não bateram, e aí o certo é abrir OUTRO desafio: o `sid` já
    usado não serve mais.
    """
    with _cliente(tempo_limite, desafio.cookies) as cliente:
        resposta = cliente.post(
            desafio.url,
            data={**desafio.campos, "txChave": chave, "captcha_response": captcha},
            headers={"Referer": desafio.url})
        resposta.raise_for_status()
        html = resposta.text

    if "sefa-sped-form" in html or "captcha_response" in html:
        raise CaptchaRecusado("o portal recusou as letras do captcha")
    if _diz_que_nao_achou(html):
        raise ChaveNaoAchada("o portal não achou NFC-e com essa chave")
    return html


def _diz_que_nao_achou(html: str) -> bool:
    baixo = html.lower()
    return any(frase in baixo for frase in (
        "não consta", "nao consta", "não foi localizada", "nao foi localizada",
        "inexistente", "não encontrada", "nao encontrada"))


def interpretar_resposta(html: str) -> Cupom:
    """Cupom a partir do HTML da consulta completa.

    O portal serve duas telas diferentes para a mesma nota: o DANFE da rota do
    QR e a consulta completa do SPED. Ambos são suportados por `interpretar`.
    Quando nem itens nem totais são reconhecidos, levanta `LayoutDesconhecido`
    para a nota cair na fila de revisão sem perder o HTML cru gravado em disco.
    """
    cupom = interpretar(html)
    if not cupom.itens and cupom.total is None:
        raise LayoutDesconhecido("a consulta completa veio num layout que ainda não sei ler")
    return cupom

