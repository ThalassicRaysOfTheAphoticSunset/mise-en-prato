"""Testes de integração das rotas da API de receitas e modo cozinha."""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mise_en_prato.config import config


def _app(tmp_path, monkeypatch, *, app_mode: str = "dev"):
    monkeypatch.setenv("DADOS", str(tmp_path))
    monkeypatch.setenv("EXIGIR_TAILNET", "false")
    monkeypatch.setenv("APP_MODE", app_mode)
    config.cache_clear()
    from mise_en_prato.main import app

    return app


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, app_mode="dev")) as c:
        yield c
    config.cache_clear()


def test_listar_receitas_vazio(cliente):
    r = cliente.get("/api/receitas")
    assert r.status_code == 200
    dados = r.json()
    assert "receitas" in dados
    assert "categorias" in dados
    assert dados["receitas"] == []


def test_criar_e_obter_receita(cliente):
    payload = {
        "nome": "Strogonoff de Frango",
        "porcoes": 4,
        "tempo_preparo_min": 30,
        "instrucoes": "Doure o frango com alho e cebola. Adicione o molho e o creme de leite.",
        "categoria": "Almoço",
        "ingredientes": [
            {
                "nome_ingrediente": "Peito de Frango",
                "quantidade": "500",
                "unidade": "g",
                "gtin_associado": "7891234567890",
            },
            {
                "nome_ingrediente": "Creme de Leite",
                "quantidade": "1",
                "unidade": "caixa",
            },
            {
                "nome_ingrediente": "Mostarda",
                "quantidade": "1.5",
                "unidade": "colher de sopa",
            },
        ],
    }

    resp = cliente.post("/api/receitas", json=payload)
    assert resp.status_code == 200
    criada = resp.json()
    rec_id = criada["id"]
    assert rec_id > 0

    # Obter por ID
    resp_detalhe = cliente.get(f"/api/receitas/{rec_id}")
    assert resp_detalhe.status_code == 200
    detalhe = resp_detalhe.json()
    assert detalhe["nome"] == "Strogonoff de Frango"
    assert detalhe["porcoes"] == 4
    assert detalhe["tempo_preparo_min"] == 30
    assert detalhe["categoria"] == "Almoço"
    assert len(detalhe["ingredientes"]) == 3
    assert detalhe["ingredientes"][0]["nome_ingrediente"] == "Peito de Frango"
    assert detalhe["ingredientes"][0]["quantidade"] == "500"
    assert detalhe["ingredientes"][2]["quantidade"] == "1.5"
    assert detalhe["ingredientes"][2]["quantidade_formatada"] == "1 ½"

    # Listar com filtro
    resp_lista = cliente.get("/api/receitas?categoria=Almoço")
    assert resp_lista.status_code == 200
    assert len(resp_lista.json()["receitas"]) == 1


def test_obter_receita_inexistente(cliente):
    resp = cliente.get("/api/receitas/999999")
    assert resp.status_code == 404


def test_editar_receita(cliente):
    resp_cria = cliente.post(
        "/api/receitas",
        json={"nome": "Bolo Simples", "porcoes": 6, "ingredientes": []},
    )
    rec_id = resp_cria.json()["id"]

    resp_edita = cliente.put(
        f"/api/receitas/{rec_id}",
        json={
            "nome": "Bolo de Chocolate Fofinho",
            "porcoes": 8,
            "tempo_preparo_min": 40,
            "instrucoes": "Misture tudo e asse a 180 graus.",
            "categoria": "Sobremesa",
            "ingredientes": [
                {"nome_ingrediente": "Cacau em Pó", "quantidade": "100", "unidade": "g"}
            ],
        },
    )
    assert resp_edita.status_code == 200

    resp_busca = cliente.get(f"/api/receitas/{rec_id}")
    detalhe = resp_busca.json()
    assert detalhe["nome"] == "Bolo de Chocolate Fofinho"
    assert detalhe["porcoes"] == 8
    assert len(detalhe["ingredientes"]) == 1
    assert detalhe["ingredientes"][0]["nome_ingrediente"] == "Cacau em Pó"


def test_deletar_receita(cliente):
    resp_cria = cliente.post(
        "/api/receitas",
        json={"nome": "Receita Temporária", "porcoes": 1},
    )
    rec_id = resp_cria.json()["id"]

    resp_del = cliente.delete(f"/api/receitas/{rec_id}")
    assert resp_del.status_code == 200

    assert cliente.get(f"/api/receitas/{rec_id}").status_code == 404


def test_upload_e_obter_foto_receita(cliente):
    resp_cria = cliente.post(
        "/api/receitas",
        json={"nome": "Panqueca com Foto", "porcoes": 2},
    )
    rec_id = resp_cria.json()["id"]

    # Cria imagem falsa
    img = Image.new("RGB", (200, 150), color="orange")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    bytes_img = buf.getvalue()

    resp_upload = cliente.post(
        f"/api/receitas/{rec_id}/foto",
        files={"foto": ("foto.jpg", bytes_img, "image/jpeg")},
    )
    assert resp_upload.status_code == 200
    assert resp_upload.json()["ok"] is True
    assert "foto_caminho" in resp_upload.json()

    # Faz o download da foto
    resp_foto = cliente.get(f"/api/receitas/{rec_id}/foto")
    assert resp_foto.status_code == 200
    assert resp_foto.headers["content-type"] == "image/webp"
    assert "public" in resp_foto.headers["cache-control"]

    # Remove foto
    resp_del_foto = cliente.delete(f"/api/receitas/{rec_id}/foto")
    assert resp_del_foto.status_code == 200
    assert cliente.get(f"/api/receitas/{rec_id}/foto").status_code == 404


def test_parse_ingredientes_endpoint(cliente):
    resp = cliente.post(
        "/api/receitas/parse-ingredientes",
        json={"texto": "2 xícaras de farinha\n500g de frango\nSal a gosto"},
    )
    assert resp.status_code == 200
    itens = resp.json()["ingredientes"]
    assert len(itens) == 3
    assert itens[0]["nome_ingrediente"] == "farinha"
    assert itens[0]["quantidade"] == "2"
    assert itens[1]["quantidade"] == "500"
    assert itens[2]["unidade"] == "a gosto"


def test_enviar_ingredientes_para_lista(cliente):
    # Cria receita com 2 ingredientes
    resp_cria = cliente.post(
        "/api/receitas",
        json={
            "nome": "Risoto de Queijo",
            "porcoes": 2,
            "ingredientes": [
                {"nome_ingrediente": "Arroz Arbóreo", "quantidade": "300", "unidade": "g"},
                {"nome_ingrediente": "Queijo Parmesão", "quantidade": "150", "unidade": "g"},
            ],
        },
    )
    rec_id = resp_cria.json()["id"]
    detalhe = cliente.get(f"/api/receitas/{rec_id}").json()
    id_queijo = detalhe["ingredientes"][1]["id"]

    # Envia apenas o queijo para uma nova lista
    resp_envia = cliente.post(
        f"/api/receitas/{rec_id}/enviar-para-lista",
        json={"nome_nova_lista": "Compras do Risoto", "ingrediente_ids": [id_queijo]},
    )
    assert resp_envia.status_code == 200
    res_envio = resp_envia.json()
    assert res_envio["ok"] is True
    assert res_envio["adicionados"] == 1
    lista_id = res_envio["lista_id"]

    # Verifica na API de lista de compras se o item está lá
    resp_lista = cliente.get(f"/api/lista/{lista_id}")
    assert resp_lista.status_code == 200
    itens_lista = resp_lista.json()["itens"]
    assert len(itens_lista) == 1
    desc = itens_lista[0]["descricao"].upper()
    assert "PARMESÃO" in desc or "PARMESAO" in desc


def test_bloqueio_escrita_em_modo_demo(tmp_path, monkeypatch):
    with TestClient(_app(tmp_path, monkeypatch, app_mode="demo")) as c:
        # Criar receita bloqueado
        resp_cria = c.post("/api/receitas", json={"nome": "Teste Demo"})
        assert resp_cria.status_code == 403
        assert "demonstração" in resp_cria.json()["detail"].lower()

        # Editar bloqueado
        resp_edita = c.put("/api/receitas/1", json={"nome": "Teste Demo"})
        assert resp_edita.status_code == 403

        # Deletar bloqueado
        resp_del = c.delete("/api/receitas/1")
        assert resp_del.status_code == 403

        # Upload bloqueado
        resp_foto = c.post(
            "/api/receitas/1/foto",
            files={"foto": ("foto.jpg", b"teste", "image/jpeg")},
        )
        assert resp_foto.status_code == 403
