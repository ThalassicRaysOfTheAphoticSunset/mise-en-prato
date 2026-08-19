#!/usr/bin/env python3
"""Ponto de entrada do contêiner: `python -m cupomzinho`.

Existe para o servidor prender ele mesmo no endereço que o .env manda. A
alternativa — deixar o Docker publicar a porta — não serve aqui: em rede
bridge o Docker mascara o IP de origem dos clientes externos, e o contêiner
passa a ver o gateway da bridge no lugar do IP da tailnet. Sem o IP real não
há `whois`, e sem `whois` não há morador.
"""
import uvicorn

from .config import config


def main() -> None:
    cfg = config()
    uvicorn.run("cupomzinho.main:app", host=cfg.escutar, port=cfg.porta,
                proxy_headers=False, forwarded_allow_ips=[])


if __name__ == "__main__":
    main()
