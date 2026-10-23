-- Suporte a linhas de produto com múltiplos sabores e variantes de código de barras (GTIN/EAN).

CREATE TABLE produto_variante (
    produto_id      INTEGER NOT NULL REFERENCES produto(id) ON DELETE CASCADE,
    gtin            TEXT NOT NULL,
    nome            TEXT NOT NULL,
    sabor           TEXT,
    marca           TEXT,
    nutriscore      TEXT,
    nova_group      INTEGER,
    foto_url        TEXT,
    eh_padrao       INTEGER NOT NULL DEFAULT 0,
    origem          TEXT NOT NULL DEFAULT 'catalogo',
    criado_em       TEXT NOT NULL,
    PRIMARY KEY (produto_id, gtin)
);

CREATE INDEX idx_variante_produto ON produto_variante(produto_id);
CREATE INDEX idx_variante_gtin ON produto_variante(gtin);

-- Backfill: produtos que já possuem GTIN ganham sua primeira variante padrão correspondente
INSERT OR IGNORE INTO produto_variante (
    produto_id, gtin, nome, marca, nutriscore, nova_group, foto_url, eh_padrao, origem, criado_em
)
SELECT p.id, p.gtin, p.descricao_canonica, p.marca, p.nutriscore, p.nova_group, p.foto_url, 1, 'migracao', datetime('now', 'localtime')
  FROM produto p
 WHERE p.gtin IS NOT NULL;
