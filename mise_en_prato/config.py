#!/usr/bin/env python3
"""Configuração da app, lida do ambiente e do .env.

Mesmo hábito do resto da casa: `.env` fora do git, `.env.exemplo` versionado
explicando cada variável. Todos os padrões apontam para os caminhos de dentro
do contêiner, e todos podem ser sobrescritos para rodar na mão no host.
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── onde ficam as coisas ─────────────────────────────────────────────────
    dados: Path = Path("/dados")
    porta: int = 8090
    # Em que endereço prender. Em produção é o IP da tailnet, e quem prende é o
    # PRÓPRIO uvicorn, não a publicação de porta do Docker — ver o compose.
    escutar: str = "0.0.0.0"

    # ── a busca no Menor Preço ───────────────────────────────────────────────
    # LOCAL é o centro da busca, "lat,lon". O padrão é Medianeira; trocar de
    # cidade é trocar esta linha.
    local: str = "-25.2947,-54.0940"
    raio_km: int = 15
    # A API satura em ~7 dias de janela e PENDURA A CONEXÃO com data=30.
    # Medido na sondagem; não aumentar sem testar de novo.
    dias_janela: int = 7
    # Teto por hora. 50 dá uma consulta a cada ~72 segundos, que é ritmo de
    # gente pesquisando uma compra grande no app oficial — dentro do que o
    # termo de uso descreve como humanamente possível num navegador.
    #
    # O que derrubou a API na construção não foi o volume por hora e sim a
    # assinatura da requisição: com o User-Agent padrão do Python ela servia
    # base de teste já nas primeiras dezenas de consultas; com o cliente
    # identificado, dado limpo. Mesmo assim o teto fica, porque é ele que
    # impede a app de virar o robô que o termo proíbe.
    teto_consultas_hora: int = 50

    # ── quem é morador ───────────────────────────────────────────────────────
    # "login_tailscale=Nome,login_tailscale=Nome". Sem senha nova: quem diz o
    # login é o tailscaled, pelo whois do IP de origem.
    moradores: str = ""
    tailscaled_sock: Path = Path("/var/run/tailscale/tailscaled.sock")
    # Desligar SÓ para desenvolver no host, onde não há tailnet na frente.
    exigir_tailnet: bool = True

    @property
    def banco(self) -> Path:
        return self.dados / "mise_en_prato.db"

    @property
    def banco_catalogo(self) -> Path:
        return self.dados / "catalogo.db"

    @property
    def pasta_cupons(self) -> Path:
        """O HTML cru de cada cupom, para reprocessar sem rebaixar a SEFAZ."""
        return self.dados / "cupons"

    @property
    def pasta_backups(self) -> Path:
        return self.dados / "backups"

    @property
    def mapa_moradores(self) -> dict[str, str]:
        mapa = {}
        for pedaco in self.moradores.split(","):
            if "=" in pedaco:
                login, nome = pedaco.split("=", 1)
                mapa[login.strip()] = nome.strip()
        return mapa


@lru_cache
def config() -> Config:
    return Config()
