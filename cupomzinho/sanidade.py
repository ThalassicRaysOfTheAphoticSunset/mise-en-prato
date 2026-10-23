#!/usr/bin/env python3
"""Decidir se uma resposta do Menor Preço é dado de verdade.

Existe porque a API não erra em voz alta. Quando ela classifica o cliente como
robô, responde **HTTP 200 com o formato certo e conteúdo de uma base de teste**
— e gravar aquilo estragaria o histórico de preço da casa em silêncio, que é a
pior forma de estragar.

Os sinais abaixo foram medidos em 19/08/2026, comparando respostas sadias com
respostas degradadas capturadas de verdade (as duas estão em tests/fixtures/):

    ncm              9 dígitos na degradada; NCM tem 8
    uf               ES e PE numa busca de 15 km em Medianeira
    uniformidade     50 resultados de 30 lojas com UMA descrição só
    dígito do gtin   código de barras que não fecha no dígito verificador

O eco do termo — descrição igual, letra por letra, ao que se consultou — é o
sinal mais forte de todos, mas SÓ vale com um termo impossível. Buscar pelo
nome exato de um produto faz a API sadia devolver exatamente aquele nome, que é
o nome dele; a regra não distingue as duas coisas e recusava dado bom. Por isso
o eco mora em `canario_passou()`, aplicado a um termo que não existe, e não em
`avaliar()`. Custou três consultas legítimas recusadas para isso ficar claro.

E um sinal que NÃO serve, e está aqui escrito para ninguém tentar de novo:
`distkm` vem coerente com o raio pedido também na resposta falsa.

A postura é recusar a resposta INTEIRA. Não se separa joio de trigo aqui: se um
sinal disparou, a origem não é confiável e nada dela entra no banco.
"""
import re
from dataclasses import dataclass, field

from .nfce_pr import normalizar

UF_ESPERADA = "PR"          # é a plataforma do Paraná; loja de outro estado é sinal
MINIMO_PARA_UNIFORMIDADE = 10


@dataclass
class Veredito:
    confiavel: bool
    motivos: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.confiavel


def digito_gtin_confere(gtin: str) -> bool:
    """Dígito verificador GS1. Aceita GTIN-8, 12, 13 e 14."""
    digitos = re.sub(r"\D", "", gtin or "")
    if len(digitos) not in (8, 12, 13, 14):
        return False
    corpo, verificador = digitos[:-1], int(digitos[-1])
    # pesos 3,1,3,1… a partir do dígito imediatamente à esquerda do verificador
    soma = sum(int(d) * (3 if k % 2 == 0 else 1) for k, d in enumerate(reversed(corpo)))
    return (10 - soma % 10) % 10 == verificador


def canario_passou(resposta: dict, termo: str) -> Veredito:
    """A prova do termo impossível: resposta sadia devolve ZERO resultado.

    Só se usa com um termo que não existe em produto nenhum. É aqui que o eco
    é sinal — e só aqui.
    """
    produtos = resposta.get("produtos") or []
    if not produtos:
        return Veredito(True)
    alvo = normalizar(termo)
    ecos = sum(1 for p in produtos if normalizar(p.get("desc", "")) == alvo)
    if ecos > len(produtos) / 2:
        return Veredito(False, [
            f"{ecos} de {len(produtos)} descrições são o eco literal de um termo inexistente"])
    return Veredito(False, [f"{len(produtos)} resultados para um termo que não existe"])


def avaliar(resposta: dict, *, gtin: str = "") -> Veredito:
    """Diz se dá para acreditar nesta resposta. Recusa é sempre da resposta toda."""
    motivos: list[str] = []
    produtos = resposta.get("produtos") or []
    if not produtos:
        # Zero resultado é resposta legítima.
        return Veredito(True)

    # 1. NCM tem 8 dígitos. A degradada devolveu 9.
    ncms = {re.sub(r"\D", "", p.get("ncm") or "") for p in produtos}
    tortos = {n for n in ncms if n and len(n) != 8}
    if tortos:
        motivos.append(f"ncm com tamanho impossível: {sorted(tortos)[:3]}")

    # 2. plataforma do Paraná não devolve loja de outro estado
    ufs = {(p.get("estabelecimento") or {}).get("uf") for p in produtos} - {None, ""}
    if ufs - {UF_ESPERADA}:
        motivos.append(f"estabelecimento fora do {UF_ESPERADA}: {sorted(ufs - {UF_ESPERADA})}")

    # 3. muitos resultados, muitas lojas, uma descrição só: dado real varia
    if len(produtos) >= MINIMO_PARA_UNIFORMIDADE:
        descricoes = {p.get("desc") for p in produtos}
        lojas = {(p.get("estabelecimento") or {}).get("nm_emp") for p in produtos}
        if len(descricoes) == 1 and len(lojas) > 3:
            motivos.append(
                f"{len(produtos)} resultados de {len(lojas)} lojas com uma descrição só")

    # 4. código de barras que não fecha, ou que não é o pedido
    codigos = {re.sub(r"\D", "", p.get("gtin") or "") for p in produtos} - {""}
    invalidos = {c for c in codigos if not digito_gtin_confere(c)}
    if invalidos:
        motivos.append(f"gtin com dígito verificador errado: {sorted(invalidos)[:3]}")
    if gtin:
        pedido = re.sub(r"\D", "", gtin).lstrip("0")
        if any(c.lstrip("0") != pedido for c in codigos):
            motivos.append("resultado com gtin diferente do consultado")

    return Veredito(not motivos, motivos)
