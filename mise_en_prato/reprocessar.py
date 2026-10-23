#!/usr/bin/env python3
"""CLI para reprocessar cupons HTML salvos em disco (dados/cupons/*.html).

Útil quando o parser evolui (por exemplo, ao adicionar suporte ao layout completo
do SPED ou novos campos fiscais) e queremos atualizar o banco local sem fazer
nenhuma chamada externa à SEFAZ.

Uso:
    python -m mise_en_prato.reprocessar [--so-incompletos] [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path

from .banco import conectar
from .config import config
from .cupons import gravar, ler
from .nfce_pr import LayoutDesconhecido, interpretar

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("mise_en_prato.reprocessar")


def reprocessar(
    pasta: Path,
    caminho_banco: Path,
    so_incompletos: bool = False,
    dry_run: bool = False,
) -> dict:
    """Varre a pasta de cupons brutos e reaplica o parser contra o banco."""
    con = conectar(caminho_banco)
    arquivos = sorted(pasta.glob("*.html"))
    total = len(arquivos)
    recuperados = 0
    erros = 0
    inalterados = 0

    log.info("Iniciando reprocessamento de %d cupons em %s...", total, pasta)

    for arq in arquivos:
        chave = arq.stem
        guardado = ler(con, chave)

        if so_incompletos and guardado and guardado["completo"]:
            inalterados += 1
            continue

        html = arq.read_text(encoding="utf-8", errors="replace")
        try:
            cupom = interpretar(html)
        except LayoutDesconhecido as erro:
            log.warning("[%s] Layout desconhecido: %s", chave, erro)
            erros += 1
            continue
        except Exception as erro:
            log.error("[%s] Falha ao interpretar: %s", chave, erro)
            erros += 1
            continue

        era_incompleto = bool(guardado and not guardado["completo"])
        agora_completo = bool(cupom.completo)

        if dry_run:
            if era_incompleto and agora_completo:
                log.info("[DRY-RUN] [%s] Seria recuperado (%d itens)", chave, len(cupom.itens))
                recuperados += 1
            else:
                inalterados += 1
            continue

        morador = guardado.get("morador") if guardado else "reprocessar"
        origem = "reprocessar"
        gravar(con, cupom, morador=morador or "reprocessar", origem=origem)

        if era_incompleto and agora_completo:
            log.info("[%s] Recuperado com sucesso! (%d itens)", chave, len(cupom.itens))
            recuperados += 1
        else:
            inalterados += 1

    log.info(
        "Fim do reprocessamento. Total: %d | Recuperados: %d | Inalterados: %d | Erros: %d",
        total,
        recuperados,
        inalterados,
        erros,
    )
    return {
        "total": total,
        "recuperados": recuperados,
        "inalterados": inalterados,
        "erros": erros,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pasta",
        type=Path,
        default=config().pasta_cupons,
        help="Caminho para a pasta com os HTMLs (padrão: config.pasta_cupons)",
    )
    parser.add_argument(
        "--so-incompletos",
        action="store_true",
        help="Processar somente cupons que entraram como incompletos",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas simular sem gravar alterações no banco",
    )
    args = parser.parse_args()

    cfg = config()
    res = reprocessar(
        pasta=args.pasta,
        caminho_banco=cfg.banco,
        so_incompletos=args.so_incompletos,
        dry_run=args.dry_run,
    )
    sys.exit(0 if res["erros"] == 0 else 1)


if __name__ == "__main__":
    main()
