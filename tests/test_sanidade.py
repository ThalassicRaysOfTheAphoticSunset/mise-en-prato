"""O detector de resposta degradada, contra as duas respostas REAIS capturadas.

O erro caro aqui não é deixar passar dado ruim uma vez — é recusar dado bom
sempre, porque aí a funcionalidade some sem ninguém entender. Por isso o
primeiro teste é o falso positivo.
"""
import json
from pathlib import Path

import pytest

from mise_en_prato.sanidade import avaliar, canario_passou, digito_gtin_confere

FIXTURES = Path(__file__).parent / "fixtures"
SADIO = json.loads((FIXTURES / "menor_preco_sadio.json").read_text(encoding="utf-8"))
DEGRADADO = json.loads((FIXTURES / "menor_preco_envenenado_gtin.json").read_text(encoding="utf-8"))


def test_resposta_sadia_passa():
    """37 produtos reais de Medianeira, capturados em 19/08/2026."""
    v = avaliar(SADIO)
    assert v.confiavel, v.motivos
    assert bool(v) is True


def test_resposta_degradada_e_recusada():
    """A mesma consulta, respondida com base de teste."""
    v = avaliar(DEGRADADO, gtin="7891167011779")
    assert not v.confiavel
    assert len(v.motivos) >= 3          # vários sinais independentes, não um só


@pytest.mark.parametrize("pedaco", ["ncm", "fora do PR", "uma descrição só"])
def test_cada_sinal_aparece_no_motivo(pedaco):
    motivos = " | ".join(avaliar(DEGRADADO, gtin="7891167011779").motivos)
    assert pedaco in motivos


def test_resposta_vazia_e_legitima():
    assert avaliar({"produtos": [], "total": 0}).confiavel


def test_buscar_pelo_nome_exato_do_produto_nao_e_eco():
    """O falso positivo que custou três consultas legítimas recusadas.

    Procurando "ATUM GOM COSTA RALADO NAT 170G", a API sadia devolve produtos
    chamados exatamente assim — porque é o nome deles. A regra do eco não
    distinguia isso de fraude, e por isso saiu de `avaliar`.
    """
    resposta = {"produtos": [
        {"desc": "ATUM GOM COSTA RALADO NAT 170G", "ncm": "16042010",
         "gtin": "7891167011779",
         "estabelecimento": {"uf": "PR", "nm_emp": f"MERCADO {k}", "mun": "MEDIANEIRA"}}
        for k in range(4)]}
    assert avaliar(resposta).confiavel


# ── o canário: só ali o eco é sinal ──────────────────────────────────────────
def test_canario_sadio_devolve_zero():
    assert canario_passou({"produtos": [], "total": 0}, "ZZQQXX FOOBAR 999G")


def test_canario_reprova_o_eco():
    eco = {"produtos": [
        {"desc": "ZZQQXX FOOBAR 999G", "ncm": "04011010", "gtin": "",
         "estabelecimento": {"uf": "PR", "nm_emp": f"LOJA {k}"}} for k in range(6)]}
    v = canario_passou(eco, "ZZQQXX FOOBAR 999G")
    assert not v and "eco literal" in v.motivos[0]


def test_canario_reprova_qualquer_resultado():
    """Termo que não existe não pode devolver produto nenhum, ecoado ou não."""
    qualquer = {"produtos": [
        {"desc": "OUTRA COISA", "ncm": "04011010", "gtin": "",
         "estabelecimento": {"uf": "PR", "nm_emp": "LOJA"}}]}
    assert not canario_passou(qualquer, "ZZQQXX FOOBAR 999G")


def test_um_sinal_sozinho_ja_derruba():
    """Recusa é da resposta inteira: não se separa joio de trigo."""
    quase_bom = json.loads(json.dumps(SADIO))
    quase_bom["produtos"][0]["estabelecimento"]["uf"] = "SC"
    assert not avaliar(quase_bom).confiavel


def test_ncm_de_9_digitos_derruba():
    quase_bom = json.loads(json.dumps(SADIO))
    quase_bom["produtos"][0]["ncm"] = "320078169"
    v = avaliar(quase_bom)
    assert not v.confiavel and "ncm" in v.motivos[0]


def test_gtin_diferente_do_consultado_derruba():
    resposta = {"produtos": [
        {"desc": "OUTRA COISA", "ncm": "04011010", "gtin": "7891000100103",
         "estabelecimento": {"uf": "PR", "nm_emp": "LOJA"}}]}
    v = avaliar(resposta, gtin="7891167011779")
    assert not v.confiavel and "diferente do consultado" in " ".join(v.motivos)


# ── dígito verificador GS1 ───────────────────────────────────────────────────
@pytest.mark.parametrize("gtin", [
    "7891167011779",    # atum Gomes da Costa, do cupom real
    "7891000100103",    # leite Moça
    "07891167011779",   # com o zero à esquerda que a API devolve
])
def test_gtin_valido(gtin):
    assert digito_gtin_confere(gtin)


@pytest.mark.parametrize("gtin", [
    "7891167011778",    # último dígito trocado
    "789116701177",     # tamanho que não existe
    "", "abc", "320078169",
])
def test_gtin_invalido(gtin):
    assert not digito_gtin_confere(gtin)


def test_gtin_da_fixture_sadia_confere():
    """Todo código de barras que a API sadia devolveu tem de fechar."""
    codigos = {p["gtin"].lstrip("0") for p in SADIO["produtos"] if p.get("gtin")}
    assert codigos, "a fixture precisa ter códigos de barras"
    assert all(digito_gtin_confere(c) for c in codigos)
