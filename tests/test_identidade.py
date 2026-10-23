"""O portão de identidade: só morador da tailnet entra."""
import pytest

from mise_en_prato.identidade import NaoIdentificado, quem_e


@pytest.fixture(autouse=True)
def _tailnet_ligada(monkeypatch):
    monkeypatch.setenv("EXIGIR_TAILNET", "true")
    monkeypatch.setenv("MORADORES", "fulano@provedor.com=Fulano")
    from mise_en_prato.config import config
    config.cache_clear()
    yield
    config.cache_clear()


@pytest.mark.parametrize("ip", [
    "172.24.0.1",     # gateway da bridge do Docker — o sintoma que apareceu no celular
    "192.168.1.50",   # LAN de casa: visita no Wi-Fi não é moradora
    "127.0.0.1",
    "100.0.0.1",      # 100.0.0.0/10 é internet pública, NÃO é tailnet
    "100.128.0.1",    # 100.128.0.0/9 idem
    "nao-e-um-ip",
])
@pytest.mark.asyncio
async def test_fora_da_faixa_da_tailnet(ip):
    """A tailnet é 100.64.0.0/10, e não "100.*" inteiro.

    Aceitar o prefixo "100." deixaria entrar endereço público de verdade.
    """
    with pytest.raises(NaoIdentificado, match="não é endereço da tailnet"):
        await quem_e(ip, 40000)


@pytest.mark.asyncio
async def test_dentro_da_faixa_chega_a_perguntar_ao_tailscaled(tmp_path, monkeypatch):
    """IP válido passa da checagem de faixa e vai falar com o tailscaled.

    Sem tailscaled do outro lado do socket, o erro tem de ser sobre o whois —
    o que prova que a faixa foi aceita.
    """
    monkeypatch.setenv("TAILSCALED_SOCK", str(tmp_path / "inexistente.sock"))
    from mise_en_prato.config import config
    config.cache_clear()
    with pytest.raises(NaoIdentificado, match="whois"):
        await quem_e("100.114.252.81", 40000)
