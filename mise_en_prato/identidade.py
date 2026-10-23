#!/usr/bin/env python3
"""Quem está do outro lado, sem inventar mais uma senha.

O servidor escuta só no IP da tailnet, então todo mundo que chega já passou
pelo Tailscale. Falta só perguntar ao tailscaled QUEM é aquele IP — é o que a
LocalAPI faz, pelo socket unix, sem CLI dentro do contêiner e sem privilégio
(o socket é 0666). Mesmo mecanismo que o coletor do painel usa para o `ping`.

O login que volta ("miguelvictor91") vira nome de morador pela env MORADORES.
Quem não está na lista é visita do nó compartilhado — e visita não registra
compra da casa.
"""
import ipaddress

import httpx

from .config import config

# A LocalAPI exige este Host; o endereço real é o socket unix.
HOST_LOCALAPI = "local-tailscaled.sock"

# A tailnet vive na faixa CGNAT 100.64.0.0/10, e NÃO em "100.*" inteiro —
# 100.0.0.0/10 e 100.128.0.0/9 são endereços públicos de verdade.
TAILNET = ipaddress.ip_network("100.64.0.0/10")


class NaoIdentificado(Exception):
    """A origem não é da tailnet, ou é da tailnet mas não é morador."""


async def quem_e(ip: str, porta: int) -> str:
    """Nome do morador dono do IP de origem. Levanta NaoIdentificado se não der.

    `exigir_tailnet=False` existe só para desenvolver no host, onde não há
    tailscaled na frente — nunca em produção.
    """
    cfg = config()
    if not cfg.exigir_tailnet:
        return "dev"

    try:
        na_tailnet = ipaddress.ip_address(ip) in TAILNET
    except ValueError:
        na_tailnet = False
    if not na_tailnet:
        # Sintoma clássico quando isto dispara com um IP tipo 172.x: o
        # contêiner está em rede bridge e o Docker mascarou a origem. A cura é
        # network_mode: host, com o uvicorn prendendo no IP da tailnet.
        raise NaoIdentificado(f"{ip} não é endereço da tailnet")

    transporte = httpx.AsyncHTTPTransport(uds=str(cfg.tailscaled_sock))
    async with httpx.AsyncClient(transport=transporte, base_url=f"http://{HOST_LOCALAPI}") as cli:
        try:
            resposta = await cli.get("/localapi/v0/whois", params={"addr": f"{ip}:{porta}"},
                                     timeout=5)
            resposta.raise_for_status()
        except httpx.HTTPError as erro:
            raise NaoIdentificado(f"tailscaled não respondeu o whois de {ip}: {erro}") from erro

    login = (resposta.json().get("UserProfile") or {}).get("LoginName", "")
    morador = cfg.mapa_moradores.get(login)
    if not morador:
        raise NaoIdentificado(f"{login or ip} está na tailnet, mas não é morador")
    return morador
