#!/usr/bin/env python3
"""Banco satélite de catálogo de produtos e inteligência de consumo.

Mapeia bases abertas (Open Food Facts Brasil, CMED/ANVISA) num SQLite separado
(dados/catalogo.db). Opera de forma totalmente desacoplada do banco transacional
(dados/cupomzinho.db), garantindo zero contenção de locks e respostas em <1ms.
"""
import csv
import gzip
import json
import logging
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import config
from .nfce_pr import normalizar
from .sanidade import digito_gtin_confere

log = logging.getLogger("cupomzinho.catalogo")


def conectar_catalogo(
    caminho: Path | None = None, criar: bool = False, somente_leitura: bool = False
) -> sqlite3.Connection | None:
    """Abre conexão com o banco satélite de catálogo.

    Se o arquivo não existir e criar=False, devolve None para indicar que o
    catálogo local ainda não foi inicializado.
    """
    caminho = caminho or config().banco_catalogo
    if not caminho.exists() and not criar:
        return None

    caminho.parent.mkdir(parents=True, exist_ok=True)
    if somente_leitura and caminho.exists():
        uri = f"file:{caminho.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        con = sqlite3.connect(caminho, check_same_thread=False)

    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 5000")

    if criar or not somente_leitura:
        criar_esquema_catalogo(con)

    return con


def criar_esquema_catalogo(con: sqlite3.Connection) -> None:
    """Garante as tabelas e índices necessários no banco de catálogo."""
    with con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS catalogo_produto (
                gtin        TEXT PRIMARY KEY,
                nome        TEXT NOT NULL,
                sabor       TEXT,
                marca       TEXT,
                categoria   TEXT,
                ncm         TEXT,
                nutriscore  TEXT,
                nova_group  INTEGER,
                foto_url    TEXT,
                origem      TEXT NOT NULL DEFAULT 'openfoodfacts'
            )
        """)
        try:
            con.execute("ALTER TABLE catalogo_produto ADD COLUMN sabor TEXT")
        except sqlite3.OperationalError:
            pass
        con.execute("CREATE INDEX IF NOT EXISTS idx_cat_gtin ON catalogo_produto(gtin)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_cat_nome ON catalogo_produto(nome)")


def normalizar_gtin(gtin: str) -> str:
    """Extrai somente os dígitos do código de barras."""
    return re.sub(r"\D", "", gtin or "")


def consultar_openfoodfacts_api(gtin: str, timeout: int = 5) -> dict | None:
    """Consulta o Open Food Facts online para um GTIN e devolve dicionário padronizado."""
    cod = normalizar_gtin(gtin)
    if not cod or not digito_gtin_confere(cod):
        return None
    url = f"https://world.openfoodfacts.org/api/v0/product/{cod}.json"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Cupomzinho - App Domestica "
                "(https://github.com/ThalassicRaysOfTheAphoticSunset/cupomzinho)"
            )
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
            if data.get("status") == 1 and "product" in data:
                prod = data["product"]
                prod["code"] = prod.get("code") or cod
                return parse_openfoodfacts_dict(prod)
    except Exception as erro:
        log.debug("Consulta ao OFF para %s falhou: %s", cod, erro)
    return None


buscar_metadados_gtin_off = consultar_openfoodfacts_api


def buscar_por_gtin(
    con: sqlite3.Connection, gtin: str, consultar_online_se_faltar: bool = False
) -> dict | None:
    """Busca produto no catálogo pelo GTIN/EAN, tratando variações de zeros."""
    cod = normalizar_gtin(gtin)
    if not cod:
        return None

    # Variações possíveis: exato, sem zero à esquerda (14 -> 13), ou com zero à esquerda (13 -> 14)
    candidatos = [cod]
    if len(cod) == 14 and cod.startswith("0"):
        candidatos.append(cod[1:])
    elif len(cod) == 13:
        candidatos.append("0" + cod)

    placeholders = ",".join("?" for _ in candidatos)
    r = con.execute(
        f"SELECT * FROM catalogo_produto WHERE gtin IN ({placeholders}) LIMIT 1",
        candidatos,
    ).fetchone()

    if r:
        return dict(r)

    if consultar_online_se_faltar:
        item = consultar_openfoodfacts_api(cod)
        if item:
            try:
                inserir_produtos(con, [item])
            except Exception:
                pass
            return item

    return None


def buscar_por_termo(con: sqlite3.Connection, termo: str, limite: int = 10) -> list[dict]:
    """Busca produtos no catálogo que contenham as palavras do termo."""
    norm = normalizar(termo)
    palavras = [p for p in norm.split() if len(p) >= 2]
    if not palavras:
        return []

    # Combina palavras com LIKE
    condicoes = " AND ".join("UPPER(nome) LIKE ?" for _ in palavras)
    params = [f"%{p}%" for p in palavras] + [limite]
    cursor = con.execute(
        f"SELECT * FROM catalogo_produto WHERE {condicoes} LIMIT ?",
        params,
    )
    return [dict(r) for r in cursor.fetchall()]


def inserir_produtos(con: sqlite3.Connection, produtos: list[dict]) -> int:
    """Insere ou atualiza produtos em lote no catálogo."""
    if not produtos:
        return 0

    sql = """
        INSERT INTO catalogo_produto (
            gtin, nome, sabor, marca, categoria, ncm, nutriscore, nova_group, foto_url, origem
        ) VALUES (
            :gtin, :nome, :sabor, :marca, :categoria, :ncm,
            :nutriscore, :nova_group, :foto_url, :origem
        ) ON CONFLICT(gtin) DO UPDATE SET
            nome = excluded.nome,
            sabor = COALESCE(excluded.sabor, catalogo_produto.sabor),
            marca = COALESCE(excluded.marca, catalogo_produto.marca),
            categoria = COALESCE(excluded.categoria, catalogo_produto.categoria),
            ncm = COALESCE(excluded.ncm, catalogo_produto.ncm),
            nutriscore = COALESCE(excluded.nutriscore, catalogo_produto.nutriscore),
            nova_group = COALESCE(excluded.nova_group, catalogo_produto.nova_group),
            foto_url = COALESCE(excluded.foto_url, catalogo_produto.foto_url),
            origem = excluded.origem
    """
    with con:
        con.executemany(
            sql,
            [
                {
                    "gtin": p["gtin"],
                    "nome": p["nome"].strip(),
                    "sabor": p.get("sabor"),
                    "marca": p.get("marca"),
                    "categoria": p.get("categoria"),
                    "ncm": p.get("ncm"),
                    "nutriscore": p.get("nutriscore"),
                    "nova_group": p.get("nova_group"),
                    "foto_url": p.get("foto_url"),
                    "origem": p.get("origem", "openfoodfacts"),
                }
                for p in produtos
                if p.get("gtin") and p.get("nome")
            ],
        )
    return len(produtos)


def parse_openfoodfacts_dict(raw: dict) -> dict | None:
    """Interpreta um dicionário bruto do dump ou API v0 do Open Food Facts."""
    cod = normalizar_gtin(raw.get("code") or "")
    if not cod or not digito_gtin_confere(cod):
        return None

    nome = (
        raw.get("product_name_pt")
        or raw.get("product_name")
        or raw.get("product_name_en")
        or ""
    ).strip()
    if not nome:
        return None

    marca = (raw.get("brands") or "").strip() or None
    categoria = (raw.get("categories") or "").strip() or None

    sabor_raw = (
        raw.get("flavor")
        or raw.get("flavour")
        or ""
    ).strip()
    if sabor_raw:
        sabor = sabor_raw
    else:
        generico = (raw.get("generic_name_pt") or raw.get("generic_name") or "").strip()
        sabor = extrair_sabor(generico, marca) if generico else extrair_sabor(nome, marca)

    nutriscore = (raw.get("nutriscore_grade") or "").strip().upper() or None
    if nutriscore and nutriscore not in ("A", "B", "C", "D", "E"):
        nutriscore = None

    nova = raw.get("nova_group")
    try:
        nova_group = int(nova) if nova is not None and int(nova) in (1, 2, 3, 4) else None
    except (ValueError, TypeError):
        nova_group = None

    foto_url = (
        raw.get("image_url")
        or raw.get("image_front_url")
        or raw.get("image_small_url")
        or None
    )

    return {
        "gtin": cod,
        "nome": nome,
        "sabor": sabor or None,
        "marca": marca,
        "categoria": categoria,
        "ncm": None,
        "nutriscore": nutriscore,
        "nova_group": nova_group,
        "foto_url": foto_url,
        "origem": "openfoodfacts",
    }


def parse_cmed_linha(linha: list[str], colunas: list[str]) -> dict | None:
    """Interpreta uma linha da planilha de preços de medicamentos da CMED/ANVISA."""
    mapeamento = {col.upper().strip(): idx for idx, col in enumerate(colunas)}

    idx_ean = mapeamento.get("EAN 1") or mapeamento.get("EAN") or mapeamento.get("CÓDIGO EAN")
    if idx_ean is None or idx_ean >= len(linha):
        return None

    ean_cru = linha[idx_ean].strip()
    cod = normalizar_gtin(ean_cru)
    if not cod or not digito_gtin_confere(cod):
        return None

    idx_prod = mapeamento.get("PRODUTO")
    idx_apres = mapeamento.get("APRESENTAÇÃO")
    idx_subst = mapeamento.get("SUBSTÂNCIA")
    idx_lab = mapeamento.get("LABORATÓRIO")
    idx_classe = mapeamento.get("CLASSE TERAPÊUTICA")

    nome_prod = linha[idx_prod].strip() if idx_prod is not None and idx_prod < len(linha) else ""
    apres = linha[idx_apres].strip() if idx_apres is not None and idx_apres < len(linha) else ""
    subst = linha[idx_subst].strip() if idx_subst is not None and idx_subst < len(linha) else ""
    lab = linha[idx_lab].strip() if idx_lab is not None and idx_lab < len(linha) else None
    classe = (
        linha[idx_classe].strip()
        if idx_classe is not None and idx_classe < len(linha)
        else None
    )

    partes_nome = [p for p in (nome_prod, apres) if p]
    nome_final = " - ".join(partes_nome) if partes_nome else subst
    if not nome_final:
        return None

    return {
        "gtin": cod,
        "nome": nome_final,
        "marca": lab,
        "categoria": classe or "Medicamentos",
        "ncm": "30049099",
        "nutriscore": None,
        "nova_group": None,
        "foto_url": None,
        "origem": "cmed",
    }


def ingestar_openfoodfacts_stream(
    con: sqlite3.Connection,
    caminho_arquivo: Path,
    lote_tamanho: int = 1000,
    apenas_brasil: bool = False,
) -> tuple[int, int]:
    """Lê dump JSONL (ou JSONL.GZ) do Open Food Facts em stream e grava no banco.

    Não carrega o arquivo todo na memória: processa linha por linha com baixo uso de RAM.
    """
    caminho = Path(caminho_arquivo)
    if caminho.suffix == ".gz":
        abridor = gzip.open(caminho, "rt", encoding="utf-8")
    else:
        abridor = open(caminho, encoding="utf-8")

    processados = 0
    inseridos = 0
    lote = []

    with abridor as f:
        for linha in f:
            linha_str = linha.strip()
            if not linha_str:
                continue
            processados += 1
            try:
                dado = json.loads(linha_str)
            except json.JSONDecodeError:
                continue

            if apenas_brasil:
                countries = dado.get("countries_tags", [])
                code = dado.get("code", "")
                eh_br = any("brazil" in str(c).lower() for c in countries) or code.startswith(
                    ("789", "790")
                )
                if not eh_br:
                    continue

            item = parse_openfoodfacts_dict(dado)
            if item:
                lote.append(item)

            if len(lote) >= lote_tamanho:
                inseridos += inserir_produtos(con, lote)
                lote = []

        if lote:
            inseridos += inserir_produtos(con, lote)

    return processados, inseridos


def ingestar_cmed_csv(
    con: sqlite3.Connection,
    caminho_arquivo: Path,
    delimitador: str = ";",
    lote_tamanho: int = 1000,
) -> tuple[int, int]:
    """Lê arquivo CSV da CMED/ANVISA e insere no catálogo satélite."""
    caminho = Path(caminho_arquivo)
    processados = 0
    inseridos = 0
    lote = []

    # Tenta latin-1 primeiro que é o padrão das tabelas gov.br
    for codificacao in ("latin-1", "utf-8"):
        try:
            with open(caminho, encoding=codificacao, errors="replace") as f:
                leitor = csv.reader(f, delimiter=delimitador)
                cabecalho = None
                for linha in leitor:
                    if not linha:
                        continue
                    if cabecalho is None:
                        # Procura a linha que contém SUBSTÂNCIA ou EAN
                        linha_upper = [c.upper().strip() for c in linha]
                        if any("EAN" in c or "SUBSTÂNCIA" in c for c in linha_upper):
                            cabecalho = linha
                        continue

                    processados += 1
                    item = parse_cmed_linha(linha, cabecalho)
                    if item:
                        lote.append(item)

                    if len(lote) >= lote_tamanho:
                        inseridos += inserir_produtos(con, lote)
                        lote = []

                if lote:
                    inseridos += inserir_produtos(con, lote)
            break
        except UnicodeDecodeError:
            continue

    return processados, inseridos


def estatisticas_catalogo(con: sqlite3.Connection) -> dict:
    """Devolve sumário do catálogo para a interface de saúde/diagnóstico."""
    total = con.execute("SELECT COUNT(*) FROM catalogo_produto").fetchone()[0]
    origens = {
        r["origem"]: r["qtd"]
        for r in con.execute(
            "SELECT origem, COUNT(*) AS qtd FROM catalogo_produto GROUP BY origem"
        )
    }
    novas = {
        r["nova_group"]: r["qtd"]
        for r in con.execute(
            "SELECT nova_group, COUNT(*) AS qtd FROM catalogo_produto "
            "WHERE nova_group IS NOT NULL GROUP BY nova_group ORDER BY nova_group"
        )
    }
    nutriscores = {
        r["nutriscore"]: r["qtd"]
        for r in con.execute(
            "SELECT nutriscore, COUNT(*) AS qtd FROM catalogo_produto "
            "WHERE nutriscore IS NOT NULL GROUP BY nutriscore ORDER BY nutriscore"
        )
    }
    com_foto = con.execute(
        "SELECT COUNT(*) FROM catalogo_produto WHERE foto_url IS NOT NULL"
    ).fetchone()[0]

    return {
        "total": total,
        "por_origem": origens,
        "por_nova": novas,
        "por_nutriscore": nutriscores,
        "com_foto": com_foto,
    }


def sincronizar_gtins_existentes(
    con_principal: sqlite3.Connection,
    con_catalogo: sqlite3.Connection | None = None,
    limite: int = 100,
) -> dict:
    """Sincroniza produtos que já têm GTIN com dados do catálogo satélite / Open Food Facts.

    Atualiza catalogo.db e enriquece a tabela produto (marca, nutriscore, nova_group, foto_url).
    """
    cat = con_catalogo if con_catalogo is not None else conectar_catalogo(criar=True)
    if not cat:
        return {"atualizados": 0, "total": 0}

    # Busca produtos com GTIN que ainda não têm dados nutricionais/marca
    prods = con_principal.execute(
        """
        SELECT id, descricao_canonica, gtin FROM produto
        WHERE gtin IS NOT NULL AND (nutriscore IS NULL AND nova_group IS NULL AND marca IS NULL)
        LIMIT ?
        """,
        (limite,),
    ).fetchall()

    atualizados = 0
    for p in prods:
        pid = p["id"]
        gtin = p["gtin"]
        info = buscar_por_gtin(cat, gtin, consultar_online_se_faltar=True)
        if info:
            with con_principal:
                con_principal.execute(
                    """
                    UPDATE produto
                    SET marca = COALESCE(marca, ?),
                        nutriscore = COALESCE(nutriscore, ?),
                        nova_group = COALESCE(nova_group, ?),
                        foto_url = COALESCE(foto_url, ?)
                    WHERE id = ?
                    """,
                    (
                        info.get("marca"),
                        info.get("nutriscore"),
                        info.get("nova_group"),
                        info.get("foto_url"),
                        pid,
                    ),
                )
            atualizados += 1
            log.info(
                "Produto %s (%s) enriquecido via catálogo: Nutri=%s, NOVA=%s, Marca=%s",
                pid,
                p["descricao_canonica"],
                info.get("nutriscore"),
                info.get("nova_group"),
                info.get("marca"),
            )
            time.sleep(0.3)

    return {"atualizados": atualizados, "total": len(prods)}


def extrair_sabor(nome: str, marca: str | None = None) -> str:
    """Tenta inferir o sabor/variante a partir do nome comercial."""
    if not nome:
        return ""
    texto = nome
    if marca:
        padrao_marca = re.escape(marca)
        texto = re.sub(rf"\b{padrao_marca}\b", "", texto, flags=re.IGNORECASE)

    genericos = [
        r"\bbebida\s+l[aá]ctea\b",
        r"\biogurte\b",
        r"\bprote[íi]nas?\b",
        r"\buht\b",
        r"\bfrasco\b",
        r"\bgarrafa\b",
        r"\blata\b",
        r"\bpet\b",
        r"\bpack\b",
        r"\bpct\b",
        r"\bchocolatado\b",
        r"\brefrigerante\b",
        r"\bsuco\b",
        r"\bcerveja\b",
        r"\bbarra\s+de\s+cereal\b",
        r"\bpicol[eé]\b",
        r"\bsorvete\b",
        r"\bsabor\b",
        r"\bde\b",
        r"\d+([.,]\d+)?\s*(g|gr|kg|ml|l|lt|un)\b",
    ]
    for g in genericos:
        texto = re.sub(g, "", texto, flags=re.IGNORECASE)
    sabor = re.sub(r"[-–—/,\.]", " ", texto).strip()
    sabor = re.sub(r"\s+", " ", sabor).strip()
    return sabor.title() if sabor else ""


def listar_variantes(con_principal: sqlite3.Connection, produto_id: int) -> list[dict]:
    """Retorna todas as variantes cadastradas para um produto."""
    try:
        linhas = con_principal.execute(
            """
            SELECT gtin, produto_id, nome, sabor, marca, nutriscore, nova_group, foto_url,
                   eh_padrao, origem, criado_em
              FROM produto_variante
             WHERE produto_id = ?
             ORDER BY eh_padrao DESC, sabor, nome
            """,
            (produto_id,),
        ).fetchall()
        return [dict(r) for r in linhas]
    except sqlite3.OperationalError:
        return []


def salvar_variantes_do_produto(
    con_principal: sqlite3.Connection,
    produto_id: int,
    variantes: list[dict],
    gtin_padrao: str | None = None,
    con_catalogo: sqlite3.Connection | None = None,
) -> int:
    """Salva uma ou mais variantes para o produto pai e sincroniza metadados."""
    if not variantes:
        return 0

    cat = con_catalogo if con_catalogo is not None else conectar_catalogo(somente_leitura=True)
    inseridos = 0
    gtins_processados = []

    prod_pai = con_principal.execute(
        "SELECT descricao_canonica FROM produto WHERE id = ?", (produto_id,)
    ).fetchone()
    desc_pai = prod_pai["descricao_canonica"] if prod_pai else ""

    for v in variantes:
        gtin = normalizar_gtin(v.get("gtin") or "")
        if not gtin or not digito_gtin_confere(gtin):
            continue

        info_cat = buscar_por_gtin(cat, gtin, consultar_online_se_faltar=True) if cat else None
        if not info_cat:
            info_cat = consultar_openfoodfacts_api(gtin)
            if info_cat and cat:
                try:
                    inserir_produtos(cat, [info_cat])
                except Exception:
                    pass

        nome_fornecido = (v.get("nome") or "").strip()
        nome_cat = (info_cat.get("nome") or "").strip() if info_cat else ""
        if nome_cat and (
            not nome_fornecido
            or nome_fornecido in ("Variante", desc_pai)
            or len(nome_cat) > len(nome_fornecido)
        ):
            nome = nome_cat
        else:
            nome = nome_fornecido or nome_cat or "Variante"

        marca = v.get("marca") or (info_cat.get("marca") if info_cat else None)
        nutriscore = v.get("nutriscore") or (info_cat.get("nutriscore") if info_cat else None)
        nova_group = (
            v.get("nova_group")
            if v.get("nova_group") is not None
            else (info_cat.get("nova_group") if info_cat else None)
        )
        foto_url = v.get("foto_url") or (info_cat.get("foto_url") if info_cat else None)
        sabor_cat = (info_cat.get("sabor") or "").strip() if info_cat else ""
        sabor = (v.get("sabor") or "").strip() or sabor_cat or extrair_sabor(nome, marca)

        eh_pad = 1 if (gtin_padrao and gtin == gtin_padrao) or v.get("eh_padrao") else 0

        with con_principal:
            if eh_pad == 1:
                con_principal.execute(
                    "UPDATE produto_variante SET eh_padrao = 0 WHERE produto_id = ?",
                    (produto_id,),
                )
            con_principal.execute(
                """
                INSERT INTO produto_variante (
                    produto_id, gtin, nome, sabor, marca, nutriscore,
                    nova_group, foto_url, eh_padrao, origem, criado_em
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(produto_id, gtin) DO UPDATE SET
                    nome = excluded.nome,
                    sabor = COALESCE(excluded.sabor, produto_variante.sabor),
                    marca = COALESCE(excluded.marca, produto_variante.marca),
                    nutriscore = COALESCE(excluded.nutriscore, produto_variante.nutriscore),
                    nova_group = COALESCE(excluded.nova_group, produto_variante.nova_group),
                    foto_url = COALESCE(excluded.foto_url, produto_variante.foto_url),
                    eh_padrao = CASE WHEN excluded.eh_padrao = 1
                                     THEN 1 ELSE produto_variante.eh_padrao END
                """,
                (
                    produto_id,
                    gtin,
                    nome.strip(),
                    sabor.strip() if sabor else None,
                    marca.strip() if marca else None,
                    nutriscore,
                    nova_group,
                    foto_url,
                    eh_pad,
                    v.get("origem", "catalogo"),
                ),
            )
        inseridos += 1
        gtins_processados.append(gtin)

    if not gtins_processados:
        return 0

    tem_padrao = con_principal.execute(
        "SELECT 1 FROM produto_variante WHERE produto_id = ? AND eh_padrao = 1", (produto_id,)
    ).fetchone()
    if not tem_padrao:
        primeiro_gtin = gtins_processados[0]
        with con_principal:
            con_principal.execute(
                "UPDATE produto_variante SET eh_padrao = 1 WHERE produto_id = ? AND gtin = ?",
                (produto_id, primeiro_gtin),
            )

    var_pad = con_principal.execute(
        """
        SELECT * FROM produto_variante 
         WHERE produto_id = ? 
         ORDER BY eh_padrao DESC, gtin LIMIT 1
        """,
        (produto_id,),
    ).fetchone()
    if var_pad:
        with con_principal:
            con_principal.execute(
                """
                UPDATE produto
                   SET gtin = ?,
                       marca = COALESCE(marca, ?),
                       nutriscore = COALESCE(nutriscore, ?),
                       nova_group = COALESCE(nova_group, ?),
                       foto_url = COALESCE(foto_url, ?)
                 WHERE id = ?
                """,
                (
                    var_pad["gtin"],
                    var_pad["marca"],
                    var_pad["nutriscore"],
                    var_pad["nova_group"],
                    var_pad["foto_url"],
                    produto_id,
                ),
            )

    return inseridos


def definir_variante_padrao(
    con_principal: sqlite3.Connection, produto_id: int, gtin: str
) -> bool:
    """Define uma variante específica como a padrão da linha."""
    cod = normalizar_gtin(gtin)
    var = con_principal.execute(
        "SELECT * FROM produto_variante WHERE produto_id = ? AND gtin = ?",
        (produto_id, cod),
    ).fetchone()
    if not var:
        return False

    with con_principal:
        con_principal.execute(
            "UPDATE produto_variante SET eh_padrao = 0 WHERE produto_id = ?",
            (produto_id,),
        )
        con_principal.execute(
            "UPDATE produto_variante SET eh_padrao = 1 WHERE produto_id = ? AND gtin = ?",
            (produto_id, cod),
        )
        con_principal.execute(
            """
            UPDATE produto
               SET gtin = ?,
                   marca = COALESCE(?, marca),
                   nutriscore = COALESCE(?, nutriscore),
                   nova_group = COALESCE(?, nova_group),
                   foto_url = COALESCE(?, foto_url)
             WHERE id = ?
            """,
            (
                cod,
                var["marca"],
                var["nutriscore"],
                var["nova_group"],
                var["foto_url"],
                produto_id,
            ),
        )
    return True


def remover_variante(
    con_principal: sqlite3.Connection, produto_id: int, gtin: str
) -> bool:
    """Remove uma variante do produto. Se for a padrão, promove outra."""
    cod = normalizar_gtin(gtin)
    with con_principal:
        con_principal.execute(
            "DELETE FROM produto_variante WHERE produto_id = ? AND gtin = ?",
            (produto_id, cod),
        )
        restante = con_principal.execute(
            "SELECT * FROM produto_variante WHERE produto_id = ? ORDER BY eh_padrao DESC LIMIT 1",
            (produto_id,),
        ).fetchone()
        if restante:
            con_principal.execute(
                "UPDATE produto_variante SET eh_padrao = 1 WHERE produto_id = ? AND gtin = ?",
                (produto_id, restante["gtin"]),
            )
            con_principal.execute(
                "UPDATE produto SET gtin = ? WHERE id = ?",
                (restante["gtin"], produto_id),
            )
        else:
            con_principal.execute(
                "UPDATE produto SET gtin = NULL WHERE id = ?",
                (produto_id,),
            )
    return True


def main():
    """CLI para gestão e ingestão de dumps no catálogo satélite."""
    import argparse

    parser = argparse.ArgumentParser(description="Gestão do Catálogo Satélite do Cupomzinho")
    sub = parser.add_subparsers(dest="comando")

    p_off = sub.add_parser(
        "ingestar-off", help="Ingesta dump JSONL (.jsonl ou .jsonl.gz) do Open Food Facts"
    )
    p_off.add_argument("arquivo", type=Path, help="Caminho do arquivo JSONL")
    p_off.add_argument("--brasil", action="store_true", help="Filtrar somente produtos do Brasil")

    p_cmed = sub.add_parser("ingestar-cmed", help="Ingesta CSV da tabela de medicamentos CMED")
    p_cmed.add_argument("arquivo", type=Path, help="Caminho do arquivo CSV")
    p_cmed.add_argument("--delimitador", default=";", help="Delimitador do CSV (padrão ';')")

    p_buscar = sub.add_parser("buscar", help="Busca produto por GTIN ou termo")
    p_buscar.add_argument("termo", help="GTIN ou texto")

    p_sync = sub.add_parser(
        "sincronizar-gtins",
        help="Sincroniza produtos com GTIN do banco principal contra o catálogo/OFF",
    )
    p_sync.add_argument(
        "--limite", type=int, default=100, help="Limite de produtos a processar"
    )

    sub.add_parser("status", help="Exibe estatísticas do catálogo")

    args = parser.parse_args()
    con = conectar_catalogo(criar=True)

    if args.comando == "ingestar-off":
        print(f"Lendo {args.arquivo}...")
        proc, ins = ingestar_openfoodfacts_stream(con, args.arquivo, apenas_brasil=args.brasil)
        print(f"Concluído: {proc} linhas processadas, {ins} produtos inseridos.")
    elif args.comando == "ingestar-cmed":
        print(f"Lendo {args.arquivo}...")
        proc, ins = ingestar_cmed_csv(con, args.arquivo, delimitador=args.delimitador)
        print(f"Concluído: {proc} linhas processadas, {ins} medicamentos inseridos.")
    elif args.comando == "buscar":
        achado = buscar_por_gtin(con, args.termo, consultar_online_se_faltar=True)
        if achado:
            print("Encontrado por GTIN:", achado)
        else:
            termos = buscar_por_termo(con, args.termo)
            print(f"Encontrados {len(termos)} resultados por termo:")
            for t in termos:
                marca_str = t.get("marca") or "s/ marca"
                print(f"  [{t['gtin']}] {t['nome']} ({marca_str}) - NOVA: {t.get('nova_group')}")
    elif args.comando == "sincronizar-gtins":
        from .banco import conectar as conectar_principal

        con_p = conectar_principal()
        res = sincronizar_gtins_existentes(con_p, con, limite=args.limite)
        print(f"Sincronização concluída: {res['atualizados']} de {res['total']} atualizados.")
        con_p.close()
    elif args.comando == "status":
        stats = estatisticas_catalogo(con)
        print("Status do Catálogo Satélite:")
        print(json.dumps(stats, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
