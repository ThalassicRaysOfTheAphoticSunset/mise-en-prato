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
                marca       TEXT,
                categoria   TEXT,
                ncm         TEXT,
                nutriscore  TEXT,
                nova_group  INTEGER,
                foto_url    TEXT,
                origem      TEXT NOT NULL DEFAULT 'openfoodfacts'
            )
        """)
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
    url = f"https://world.openfoodfacts.org/api/v2/product/{cod}.json"
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
            gtin, nome, marca, categoria, ncm, nutriscore, nova_group, foto_url, origem
        ) VALUES (
            :gtin, :nome, :marca, :categoria, :ncm, :nutriscore, :nova_group, :foto_url, :origem
        ) ON CONFLICT(gtin) DO UPDATE SET
            nome = excluded.nome,
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
    """Interpreta um dicionário bruto do dump JSONL do Open Food Facts."""
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

    nutriscore = (raw.get("nutriscore_grade") or "").strip().upper() or None
    if nutriscore and nutriscore not in ("A", "B", "C", "D", "E"):
        nutriscore = None

    nova = raw.get("nova_group")
    try:
        nova_group = int(nova) if nova is not None and int(nova) in (1, 2, 3, 4) else None
    except (ValueError, TypeError):
        nova_group = None

    foto_url = (
        raw.get("image_front_url")
        or raw.get("image_url")
        or raw.get("image_small_url")
        or None
    )

    return {
        "gtin": cod,
        "nome": nome,
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
