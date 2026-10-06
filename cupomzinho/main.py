#!/usr/bin/env python3
"""O servidor do cupomzinho: a API e a PWA, no mesmo processo.

    uvicorn cupomzinho.main:app --host 0.0.0.0 --port 8090

Escuta só no IP da tailnet (quem publica a porta é o compose), então todo mundo
que chega já passou pelo Tailscale — falta apenas perguntar ao tailscaled quem
é. Ver identidade.py.
"""
import asyncio
import json
import logging
import re
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    cupons,
    enriquecimento,
    listas,
    menor_preco,
    relatorios,
    tarefas,
    unificacao,
)
from .banco import conectar
from .config import config
from .consulta_chave import (
    CaptchaRecusado,
    ChaveNaoAchada,
    Desafio,
    LayoutDesconhecido,
    PortalMudou,
    abrir_desafio,
    consultar,
    interpretar_resposta,
)
from .identidade import NaoIdentificado, quem_e
from .nfce_pr import (
    Cupom,
    Estabelecimento,
    buscar,
    chave_da_url,
    cnpj_formatado,
    dados_da_chave,
    dinheiro,
    interpretar,
    validar_chave,
)
from .qr import ler_qr

WEB = Path(__file__).parent / "web"
log = logging.getLogger("cupomzinho.main")


class Codificador(json.JSONEncoder):
    """Decimal vira string no JSON, não float.

    Float aqui reintroduziria justamente o erro de centavo que o banco em
    inteiros existe para evitar; a tela formata a string como quiser.
    """

    def default(self, o):
        return str(o) if isinstance(o, Decimal) else super().default(o)


class RespostaJSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, cls=Codificador, ensure_ascii=False).encode("utf-8")


def _guardar_falha(bruto: bytes, nome: str) -> Path:
    """Guarda a imagem que não deu QR, para virar caso de teste."""
    pasta = config().dados / "falhas"
    pasta.mkdir(parents=True, exist_ok=True)
    sufixo = Path(nome).suffix or ".bin"
    alvo = pasta / f"{datetime.now():%Y-%m-%d_%H%M%S}{sufixo}"
    alvo.write_bytes(bruto)
    return alvo


def responder(dados) -> RespostaJSON:
    """Devolve a resposta JÁ SERIALIZADA, e é de propósito.

    Se a rota devolvesse um dict, o FastAPI passaria antes pelo jsonable_encoder
    dele — que transforma Decimal em FLOAT. Isso reintroduziria, na última
    curva, exatamente o erro de centavo que o banco em inteiros existe para
    evitar. Objeto Response o FastAPI entrega intacto.
    """
    return RespostaJSON(dados)


def configurar_log() -> None:
    """Faz o log da app aparecer no `docker logs`.

    O uvicorn monta a configuração de log dele e não adota logger de terceiros,
    então sem isto todo `log.info` da app some — e some justamente na hora de
    diagnosticar, que foi o que aconteceu com a primeira foto que falhou.
    """
    log = logging.getLogger("cupomzinho")
    if log.handlers:
        return
    saida = logging.StreamHandler(sys.stdout)
    saida.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                         "%H:%M:%S"))
    log.addHandler(saida)
    log.setLevel(logging.INFO)
    log.propagate = False


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    cfg = config()
    configurar_log()
    cfg.pasta_cupons.mkdir(parents=True, exist_ok=True)
    cfg.pasta_backups.mkdir(parents=True, exist_ok=True)
    app.state.con = conectar(cfg.banco)
    # O backup vive no ciclo de vida do servidor: sobe junto, desce junto.
    app.state.tarefas = [
        asyncio.create_task(tarefas.laco_de_backup(app.state.con, cfg.pasta_backups)),
    ]
    yield
    for tarefa in app.state.tarefas:
        tarefa.cancel()
    app.state.con.close()


app = FastAPI(title="cupomzinho", default_response_class=RespostaJSON, lifespan=ciclo_de_vida)


@app.exception_handler(menor_preco.TetoAtingido)
async def teto_virou_429(_request: Request, erro: menor_preco.TetoAtingido):
    """Teto de consultas não é falha do servidor — é a regra funcionando.

    Existe como tratador global, e não como try/except numa rota, porque o teto
    pode estourar em qualquer ponto que consulte: no canário antes do lote, no
    meio dele, ou numa rota que ainda nem foi escrita. Vazando, virava 500.
    """
    return RespostaJSON({"detail": str(erro)}, status_code=429)


def banco(request: Request) -> sqlite3.Connection:
    return request.app.state.con


async def morador(request: Request) -> str:
    """Quem está pedindo. 403 para quem não é morador — inclusive visita da tailnet."""
    cliente = request.client
    if cliente is None:
        raise HTTPException(403, "origem desconhecida")
    try:
        return await quem_e(cliente.host, cliente.port)
    except NaoIdentificado as erro:
        raise HTTPException(403, str(erro)) from erro


# Apelidos das dependências. Além de deixarem a assinatura das rotas curta,
# são o estilo que o FastAPI recomenda hoje — Depends() no valor padrão do
# argumento funciona, mas é uma armadilha conhecida de Python.
Banco = Annotated[sqlite3.Connection, Depends(banco)]
Morador = Annotated[str, Depends(morador)]


# ── envio de cupom ───────────────────────────────────────────────────────────
class EnvioCupom(BaseModel):
    url: str = ""      # a URL inteira do QR Code (é dela que sai o hash de segurança)
    chave: str = ""    # só serve para reconsultar cupom já guardado


def _guardar_html(chave: str, html: str) -> None:
    """O HTML cru é gravado ANTES de interpretar.

    Quando o portal mexer no layout, o reprocessamento roda em cima disto e
    nenhum cupom se perde. Vale para as duas portas: a do QR e a da chave.
    """
    (config().pasta_cupons / f"{chave}.html").write_text(html, encoding="utf-8")


def _gravar(con, chave: str, cupom, quem: str, origem: str, url_qr: str = "") -> dict:
    """Grava o cupom já interpretado, mandando a chave informada prevalecer."""
    if cupom.chave != chave:
        cupom.avisos.append(
            f"a chave da página ({cupom.chave}) difere da chave informada ({chave})")
        cupom.chave = chave
    era_novo = cupons.gravar(con, cupom, morador=quem, origem=origem, url_qr=url_qr)
    return {"novo": era_novo, "cupom": cupons.ler(con, chave)}


def _ja_guardado(con, chave: str) -> dict | None:
    guardado = cupons.ler(con, chave) if cupons.existe(con, chave) else None
    return {"novo": False, "cupom": guardado} if guardado else None


def _chave_valida(bruta: str) -> str:
    """A chave normalizada, ou 400 explicando qual das duas coisas deu errado."""
    chave = chave_da_url(bruta)
    if not chave:
        raise HTTPException(400, "isso não tem 44 dígitos. A chave de acesso está impressa "
                                 "no cupom, em grupos de quatro, logo acima do QR Code.")
    if not validar_chave(chave):
        raise HTTPException(400, "o dígito verificador não fecha — algum número saiu trocado. "
                                 "Confira os 44 dígitos no papel e mande de novo.")
    return chave


def _registrar(con, url: str, quem: str, origem: str) -> dict:
    """Busca pela URL do QR, interpreta e grava. Idempotente pela chave."""
    chave = chave_da_url(url)
    if not chave:
        raise HTTPException(400, "não encontrei uma chave de acesso de 44 dígitos nesse link")

    if (guardado := _ja_guardado(con, chave)):
        return guardado

    try:
        html = buscar(url)
    except Exception as erro:
        raise HTTPException(502, f"a SEFAZ não respondeu: {erro}") from erro

    _guardar_html(chave, html)
    return _gravar(con, chave, interpretar(html), quem, origem, url_qr=url)


@app.post("/api/cupom")
async def enviar_cupom(envio: EnvioCupom, quem: Morador, con: Banco):
    if envio.url:
        return responder(_registrar(con, envio.url, quem, "qrcode"))
    if envio.chave:
        guardado = cupons.ler(con, envio.chave.replace(" ", ""))
        if not guardado:
            raise HTTPException(
                404,
                "não tenho esse cupom guardado. A rota do QR Code exige o link inteiro, "
                "com o hash de segurança; para entrar só com a chave, use a consulta "
                "completa do portal, em /api/cupom/chave — ela pede um captcha.")
        return responder({"novo": False, "cupom": guardado})
    raise HTTPException(400, "mande a url do QR Code ou a chave de um cupom já guardado")


@app.post("/api/cupom/foto")
async def enviar_foto(quem: Morador, con: Banco, foto: Annotated[UploadFile, File()]):
    """Foto do QR Code do cupom. Quem decodifica é o servidor.

    É isto que faz o iPhone funcionar: o Safari não tem BarcodeDetector, então
    depender do navegador deixaria metade da casa de fora.
    """
    bruto = await foto.read()
    try:
        achados = ler_qr(bruto)
    except ValueError as erro:
        _guardar_falha(bruto, foto.filename or "foto")
        raise HTTPException(400, str(erro)) from erro

    for conteudo in achados:
        if chave_da_url(conteudo):
            return responder(_registrar(con, conteudo, quem, "foto"))

    # A foto que não deu certo fica guardada. Consertar leitor de código de
    # barras sem o arquivo que falhou é adivinhação; com ele, é teste.
    guardada = _guardar_falha(bruto, foto.filename or "foto")
    log.warning("QR não encontrado; foto guardada em %s", guardada)

    if achados:
        # Achou QR, mas não era de cupom — o cupom tem outros códigos impressos.
        raise HTTPException(
            422, "achei um código nessa foto, mas não é o QR da nota fiscal. "
                 "O certo é o quadradinho embaixo, perto da chave de acesso.")
    raise HTTPException(
        422, "não achei nenhum QR Code nessa foto. A foto ficou guardada no "
             "servidor para eu descobrir por quê — tente também colar o link.")


@app.post("/api/cupom/html")
async def enviar_html(quem: Morador, con: Banco, arquivo: Annotated[UploadFile, File()]):
    """Recebe o arquivo HTML da NFC-e salvo direto do portal da SEFAZ."""
    bruto = await arquivo.read()
    try:
        html = bruto.decode("utf-8")
    except UnicodeDecodeError:
        html = bruto.decode("iso-8859-1", errors="replace")

    try:
        cupom = interpretar(html)
    except Exception as erro:
        raise HTTPException(400, f"não consegui ler o cupom desse HTML: {erro}") from erro

    if not cupom.chave or len(cupom.chave) != 44:
        match = re.search(r"\b(?:\d[\s\xa0]*){44}\b", html)
        if match:
            chave_candidata = re.sub(r"\D", "", match.group(0))
            if len(chave_candidata) == 44:
                cupom.chave = chave_candidata

    if not cupom.chave or len(cupom.chave) != 44 or not cupom.chave.isdigit():
        raise HTTPException(400, "não encontrei uma chave de acesso de 44 dígitos no HTML")

    _guardar_html(cupom.chave, html)
    return responder(_gravar(con, cupom.chave, cupom, quem, "html"))


# ── entrar pela chave, quando o QR está perdido ──────────────────────────────
# O QR manchado não é hipótese: em 26/08/2026 duas fotos morreram em
# dados/falhas/ e resistiram a 840 combinações de escala, filtro e
# decodificador. A chave, essa, continua impressa no papel — e o portal do
# estado tem uma segunda consulta que a aceita, cobrando um captcha.
#
# Os desafios abertos vivem AQUI, em memória: são sessão do portal, não dado da
# casa, e não têm o que fazer em disco. Um punhado basta, porque do outro lado
# é gente digitando letra por letra.
TETO_DESAFIOS = 10
_desafios: dict[str, Desafio] = {}


def _limpar_desafios() -> None:
    for id_desafio in [i for i, d in _desafios.items() if d.vencido]:
        _desafios.pop(id_desafio, None)
    while len(_desafios) > TETO_DESAFIOS:
        _desafios.pop(next(iter(_desafios)))


def _cupom_da_chave(con, chave: str, *, total=None, emitido_em=None, avisos: list[str]) -> Cupom:
    """O cupom que a própria chave já permite montar: loja, número, série, mês.

    Sem itens, de propósito — meia informação vale mais que nenhuma, e o
    `completo = 0` manda ele para a fila de revisão.
    """
    dados = dados_da_chave(chave)
    linha = con.execute(
        "SELECT razao FROM estabelecimento WHERE cnpj = ?", (dados["cnpj"],)).fetchone()
    # Loja já conhecida mantém o nome dela: `_garantir_estabelecimento` sobrescreve
    # a razão social, e cupom digitado não pode rebatizar o mercado de "CNPJ ...".
    razao = linha["razao"] if linha else f"CNPJ {cnpj_formatado(dados['cnpj'])}"
    return Cupom(
        chave=chave,
        estabelecimento=Estabelecimento(cnpj=dados["cnpj"], razao=razao),
        emitido_em=emitido_em,
        numero=dados["numero"], serie=dados["serie"],
        total=total, valor_pago=total,
        avisos=list(avisos))


def _total_digitado(texto: str) -> Decimal:
    bruto = (texto or "").replace("R$", "").strip()
    try:
        valor = dinheiro(bruto) if "," in bruto else Decimal(bruto)
    except (InvalidOperation, ValueError):
        valor = None
    if valor is None or valor <= 0:
        raise HTTPException(400, "quanto foi a compra? Mande o total do cupom, como 106,74.")
    return valor


def _data_digitada(texto: str, chave: str) -> datetime:
    try:
        data = datetime.fromisoformat((texto or "").strip())
    except ValueError as erro:
        raise HTTPException(400, "que dia foi a compra? A data vai como AAAA-MM-DD.") from erro
    dados = dados_da_chave(chave)
    # A chave carrega o mês da emissão. Data fora dele jogaria o gasto no mês
    # errado — e gasto no mês errado é pior que cupom faltando.
    if (data.year, data.month) != (dados["ano"], dados["mes"]):
        raise HTTPException(
            400, f"a chave diz que essa nota é de {dados['mes']:02d}/{dados['ano']}, "
                 f"e a data mandada é de {data.month:02d}/{data.year}.")
    return data


class EnvioChave(BaseModel):
    chave: str                 # os 44 dígitos, com ou sem os espaços do papel
    desafio: str               # o id devolvido por /api/chave/desafio
    captcha: str               # as letras que o morador leu na imagem
    reconsultar: bool = False  # forçar reconsulta no portal


class EnvioManual(BaseModel):
    chave: str
    total: str = ""
    emitido_em: str = ""       # AAAA-MM-DD


# As três rotas abaixo são `def` e não `async def` de propósito: falam com a
# rede de forma síncrona, e o FastAPI as manda para o threadpool em vez de
# travar o event loop de todo mundo enquanto o portal do estado pensa.
@app.get("/api/chave/desafio")
def desafio_de_captcha(quem: Morador):
    """Um captcha novo, para o morador ler e digitar.

    Quem resolve é gente. O cupomzinho só carrega a imagem até a tela.
    """
    _limpar_desafios()
    try:
        desafio = abrir_desafio()
    except PortalMudou as erro:
        raise HTTPException(502, f"o formulário do portal mudou de forma: {erro}") from erro
    except Exception as erro:
        raise HTTPException(502, f"a SEFAZ não respondeu: {erro}") from erro

    _desafios[desafio.id] = desafio
    log.info("captcha aberto para %s (%d desafios vivos)", quem, len(_desafios))
    return responder({"desafio": desafio.id, "imagem": desafio.imagem_data_uri})


@app.post("/api/cupom/chave")
def enviar_chave(envio: EnvioChave, quem: Morador, con: Banco):
    """Consulta completa pela chave digitada — a saída quando o QR não lê.

    A chave se confere sozinha, então dígito trocado é recusado ANTES de gastar
    o captcha. E o captcha, uma vez usado, não volta: erro rende desafio novo.
    """
    chave = _chave_valida(envio.chave)
    if not envio.reconsultar and (guardado := _ja_guardado(con, chave)):
        return responder(guardado)

    desafio = _desafios.pop(envio.desafio, None)
    if desafio is None or desafio.vencido:
        raise HTTPException(410, "esse captcha venceu. Peça outra imagem e mande de novo.")

    try:
        html = consultar(desafio, chave, envio.captcha.strip())
    except CaptchaRecusado as erro:
        raise HTTPException(422, "as letras do captcha não conferiram — a imagem já foi "
                                 "trocada, é só ler a nova.") from erro
    except ChaveNaoAchada as erro:
        raise HTTPException(404, "o portal não achou nota com essa chave. Confira os dígitos; "
                                 "nota recém-emitida às vezes demora a aparecer.") from erro
    except Exception as erro:
        raise HTTPException(502, f"a SEFAZ não respondeu: {erro}") from erro

    _guardar_html(chave, html)
    try:
        cupom = interpretar_resposta(html)
    except LayoutDesconhecido:
        # O HTML cru já está em disco: é dele que sai a fixture que ensina o
        # parser a ler esta tela. Até lá o cupom entra pela metade, não se perde.
        log.warning("consulta completa em layout desconhecido; HTML guardado para a chave %s",
                    chave)
        cupom = _cupom_da_chave(
            con, chave, avisos=["a consulta completa veio num layout que ainda não sei ler; "
                                "o cupom entrou sem os itens"])
    return responder(_gravar(con, chave, cupom, quem, "chave"))


@app.post("/api/cupom/manual")
def enviar_manual(envio: EnvioManual, quem: Morador, con: Banco):
    """A rede de segurança: registrar o gasto só com a chave e o total.

    Para quando nem o portal ajuda — SEFAZ fora do ar, captcha teimoso. Entra
    sem itens, com aviso, e cai na fila de revisão para ser reconsultado depois.
    """
    chave = _chave_valida(envio.chave)
    guardado = _ja_guardado(con, chave)
    if guardado and guardado["cupom"]["completo"]:
        return responder(guardado)

    cupom = _cupom_da_chave(
        con, chave,
        total=_total_digitado(envio.total),
        emitido_em=_data_digitada(envio.emitido_em, chave),
        avisos=["cupom digitado a partir da chave: sem os itens, só o total"])
    return responder(_gravar(con, chave, cupom, quem, "manual"))


# ── consulta ─────────────────────────────────────────────────────────────────
@app.get("/api/saude")
async def saude(con: Banco):
    """Sem autenticação: é o que o Uptime Kuma bate. Não devolve dado da casa."""
    return responder({"ok": True,
                      "cupons": con.execute("SELECT COUNT(*) FROM cupom").fetchone()[0]})


@app.get("/api/eu")
async def eu(quem: Morador):
    return responder({"morador": quem})


@app.get("/api/cupons")
async def listar_cupons(_: Morador, con: Banco, limite: int = 20):
    return responder({"cupons": cupons.recentes(con, limite),
                      "revisao": cupons.fila_de_revisao(con)})


@app.get("/api/cupom/{chave}")
async def ver_cupom(chave: str, _: Morador, con: Banco):
    guardado = cupons.ler(con, chave)
    if not guardado:
        raise HTTPException(404, "cupom não encontrado")
    return responder(guardado)


@app.get("/api/falhas")
async def listar_falhas(_: Morador, con: Banco):
    """Fotos que não deram leitura de QR e ficaram salvas para análise."""
    pasta = config().dados / "falhas"
    if not pasta.is_dir():
        return responder({"falhas": []})

    cupons_banco = con.execute(
        "SELECT c.chave, c.emitido_em, c.criado_em, c.completo, e.razao "
        "FROM cupom c JOIN estabelecimento e ON e.cnpj = c.cnpj"
    ).fetchall()

    fotos = []
    for arq in sorted(pasta.glob("*.jpg"), reverse=True):
        stat = arq.stat()
        data_foto = None
        try:
            data_foto = datetime.strptime(arq.stem, "%Y-%m-%d_%H%M%S")
        except ValueError:
            pass

        cupom_casado = None
        if data_foto:
            for c in cupons_banco:
                try:
                    dt_c = datetime.fromisoformat(c["criado_em"])
                    if abs((data_foto - dt_c).total_seconds()) <= 900:
                        cupom_casado = {
                            "chave": c["chave"],
                            "loja": c["razao"],
                            "completo": bool(c["completo"]),
                            "criado_em": c["criado_em"],
                        }
                        break
                except (ValueError, TypeError):
                    continue

        fotos.append({
            "arquivo": arq.name,
            "data": data_foto.isoformat(sep=" ") if data_foto else "",
            "tamanho_kb": stat.st_size // 1024,
            "cupom": cupom_casado,
        })
    return responder({"falhas": fotos})


@app.get("/api/falha/{nome}")
async def ver_foto_falha(nome: str, _: Morador):
    """Serve uma imagem salva de falha de QR Code."""
    if not re.match(r"^[\w.-]+\.(jpg|jpeg|png)$", nome, re.IGNORECASE):
        raise HTTPException(400, "nome de arquivo inválido")
    caminho = config().dados / "falhas" / nome
    if not caminho.is_file():
        raise HTTPException(404, "imagem não encontrada")
    return FileResponse(caminho, media_type="image/jpeg")


@app.get("/api/gastos")
async def gastos(_: Morador, con: Banco, mes: str = ""):
    return responder({"resumo": relatorios.resumo_do_mes(con, mes or None),
                      "meses": relatorios.meses_com_gasto(con)})


@app.get("/api/produtos")
async def listar_produtos(_: Morador, con: Banco, busca: str = ""):
    return responder({"produtos": relatorios.produtos(con, busca)})


@app.get("/api/produto/{produto_id}")
async def ver_produto(produto_id: int, _: Morador, con: Banco):
    achado = relatorios.historico_do_produto(con, produto_id)
    if not achado:
        raise HTTPException(404, "produto não encontrado")
    return responder(achado)


# ── unificar produtos ────────────────────────────────────────────────────────
class Unir(BaseModel):
    manter: int
    juntar: int


class Recusa(BaseModel):
    desc_a: str
    desc_b: str


@app.get("/api/unificar")
async def ver_candidatos(_: Morador, con: Banco):
    """Pares que parecem o mesmo produto. SUGESTÃO — nada acontece sozinho."""
    return responder({"candidatos": unificacao.candidatos(con)})


@app.post("/api/unificar")
async def unir(uniao: Unir, _: Morador, con: Banco):
    try:
        return responder(unificacao.unificar(con, uniao.manter, uniao.juntar))
    except ValueError as erro:
        raise HTTPException(400, str(erro)) from erro


@app.post("/api/unificar/recusar")
async def recusar_par(recusa: Recusa, _: Morador, con: Banco):
    """"Não são a mesma coisa" — para o par não voltar a ser sugerido."""
    unificacao.recusar(con, recusa.desc_a, recusa.desc_b)
    return responder({"ok": True})


# ── lista de compras ─────────────────────────────────────────────────────────
class NovaLista(BaseModel):
    nome: str = "compras"


class ItemDaLista(BaseModel):
    produto_id: int
    quantidade: Decimal = Decimal("1")


@app.get("/api/listas")
async def ver_listas(_: Morador, con: Banco):
    return responder({"listas": listas.listar(con), "sugestoes": listas.sugerir(con)})


@app.post("/api/listas")
async def nova_lista(dados: NovaLista, quem: Morador, con: Banco):
    return responder({"id": listas.criar(con, dados.nome, quem)})


@app.get("/api/lista/{lista_id}")
async def ver_lista(lista_id: int, _: Morador, con: Banco):
    return responder({"itens": listas.itens(con, lista_id),
                      "comparacao": listas.comparar(con, lista_id)})


@app.post("/api/lista/{lista_id}/item")
async def por_item(lista_id: int, item: ItemDaLista, _: Morador, con: Banco):
    listas.por_produto(con, lista_id, item.produto_id, item.quantidade)
    return responder({"ok": True})


@app.delete("/api/lista/{lista_id}/item/{produto_id}")
async def tirar_item(lista_id: int, produto_id: int, _: Morador, con: Banco):
    listas.tirar(con, lista_id, produto_id)
    return responder({"ok": True})


# Rota SÍNCRONA de propósito: o cliente do Menor Preço espaça as consultas com
# sleep, e num handler async isso travaria o laço de eventos inteiro. Sendo
# `def`, o FastAPI a roda numa thread e a PWA continua respondendo.
@app.post("/api/lista/{lista_id}/pesquisar")
def pesquisar_precos(lista_id: int, request: Request):
    """Consulta o Menor Preço para os itens da lista. **Só a pedido de gente.**

    É a única rota que fala com a API do estado, e ela existe porque alguém
    apertou um botão. Antes do lote vai o canário: se a API estiver servindo
    base de teste, descobrir aqui custa uma consulta em vez do lote inteiro.
    """
    con = request.app.state.con
    cliente = menor_preco.MenorPreco(con)
    relatorio = {"consultados": 0, "guardados": 0, "recusados": [], "sem_resultado": []}

    if not cliente.canario():
        raise HTTPException(
            503, "a API do Menor Preço está devolvendo base de teste agora — "
                 "nada foi gravado. Tente mais tarde.")

    for item in listas.itens(con, lista_id):
        try:
            achados = cliente.buscar(gtin=item["gtin"] or "",
                                     termo="" if item["gtin"] else item["descricao"])
        except menor_preco.TetoAtingido as erro:
            relatorio["teto"] = str(erro)
            break
        except menor_preco.RespostaSuspeita as erro:
            relatorio["recusados"].append({"item": item["descricao"], "porque": str(erro)})
            continue
        relatorio["consultados"] += 1
        if not achados:
            relatorio["sem_resultado"].append(item["descricao"])
            continue
        relatorio["guardados"] += menor_preco.guardar(con, item["produto_id"], achados)

    relatorio["comparacao"] = listas.comparar(con, lista_id)
    return responder(relatorio)


# ── descobrir código de barras ───────────────────────────────────────────────
@app.get("/api/enriquecer")
async def ver_pendentes(_: Morador, con: Banco):
    return responder({"pendentes": enriquecimento.pendentes(con)})


# Síncrona pelo mesmo motivo da pesquisa de preços: o cliente espaça as
# consultas com sleep, e num handler async isso travaria o laço de eventos.
@app.post("/api/enriquecer")
def enriquecer_produtos(request: Request, quantos: int = 5):
    """Descobre o código de barras dos produtos mais comprados. **A pedido.**

    `quantos` é baixo de propósito: cada produto custa uma consulta, e o teto
    por hora é o que mantém esta app do lado certo do termo de uso.
    """
    con = request.app.state.con
    cliente = menor_preco.MenorPreco(con)
    if not cliente.canario():
        raise HTTPException(
            503, "a API do Menor Preço está devolvendo base de teste agora — "
                 "nada foi gravado. Tente mais tarde.")

    relatorio = {"identificados": [], "sem_certeza": []}
    for pendente in enriquecimento.pendentes(con, quantos):
        try:
            r = enriquecimento.enriquecer(con, cliente, pendente["produto_id"])
        except menor_preco.TetoAtingido as erro:
            relatorio["teto"] = str(erro)
            break
        except menor_preco.RespostaSuspeita as erro:
            relatorio["sem_certeza"].append(
                {"descricao": pendente["descricao"], "motivo": f"resposta recusada: {erro}"})
            continue
        alvo = "identificados" if r["novo"] else "sem_certeza"
        relatorio[alvo].append({"descricao": r["descricao"], "gtin": r.get("gtin"),
                                "motivo": r["motivo"]})
    relatorio["restantes"] = len(enriquecimento.pendentes(con, 999))
    return responder(relatorio)


# ── a PWA ────────────────────────────────────────────────────────────────────
@app.get("/")
async def raiz():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
