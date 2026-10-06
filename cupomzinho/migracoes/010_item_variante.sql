-- Suporte a inventário doméstico fiel: associação do sabor e EAN ao item comprado,
-- e aprendizado por loja através do código interno (codigo_loja / PLU).

ALTER TABLE item ADD COLUMN gtin_variante TEXT;
ALTER TABLE item ADD COLUMN sabor_adicional TEXT;

CREATE INDEX idx_item_gtin_variante ON item(gtin_variante);

CREATE TABLE IF NOT EXISTS codigo_loja_produto (
    cnpj TEXT NOT NULL,
    codigo_loja TEXT NOT NULL,
    produto_id INTEGER NOT NULL REFERENCES produto(id) ON DELETE CASCADE,
    gtin TEXT,
    sabor TEXT,
    atualizado_em TEXT NOT NULL,
    PRIMARY KEY (cnpj, codigo_loja)
);

CREATE INDEX idx_clp_produto ON codigo_loja_produto(produto_id);
