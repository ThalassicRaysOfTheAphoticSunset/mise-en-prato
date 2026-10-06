"""Testes do CLI de reprocessamento de cupons salvos em disco."""
from pathlib import Path

from cupomzinho.banco import conectar
from cupomzinho.cupons import gravar, ler
from cupomzinho.nfce_pr import interpretar
from cupomzinho.reprocessar import reprocessar

FIXTURE_SPED = Path(__file__).parent / "fixtures" / "cupom_sped_completo.html"


def test_reprocessar_recupera_cupom_incompleto(tmp_path):
    pasta_cupons = tmp_path / "cupons"
    pasta_cupons.mkdir()
    caminho_banco = tmp_path / "teste.db"
    con = conectar(caminho_banco)

    # 1. Cria um cupom no banco incompleto
    cupom_real = interpretar(FIXTURE_SPED.read_text(encoding="utf-8"))
    cupom_incompleto = interpretar("<html><body>incompleto</body></html>")
    cupom_incompleto.chave = cupom_real.chave
    cupom_incompleto.estabelecimento = cupom_real.estabelecimento
    gravar(con, cupom_incompleto, morador="Miguel")

    assert ler(con, cupom_real.chave)["completo"] is False

    # 2. Salva o HTML do cupom real na pasta
    (pasta_cupons / f"{cupom_real.chave}.html").write_text(
        FIXTURE_SPED.read_text(encoding="utf-8"), encoding="utf-8"
    )

    # 3. Executa dry-run
    res_dry = reprocessar(pasta_cupons, caminho_banco, dry_run=True)
    assert res_dry["total"] == 1
    assert res_dry["recuperados"] == 1
    assert ler(con, cupom_real.chave)["completo"] is False  # não mudou

    # 4. Executa reprocessamento real
    res_real = reprocessar(pasta_cupons, caminho_banco, dry_run=False)
    assert res_real["total"] == 1
    assert res_real["recuperados"] == 1
    assert res_real["erros"] == 0

    # 5. Verifica se o cupom no banco agora está completo e com itens enriquecidos
    salvo = ler(con, cupom_real.chave)
    assert salvo["completo"] is True
    assert len(salvo["itens"]) == 3
