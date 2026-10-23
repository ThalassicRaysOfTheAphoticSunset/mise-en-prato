-- Suporte a receitas culinárias e ingredientes integrados ao catálogo de compras.

CREATE TABLE receitas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nome                TEXT NOT NULL,
    porcoes             INTEGER NOT NULL DEFAULT 1,
    tempo_preparo_min   INTEGER,
    instrucoes          TEXT,
    criada_em           TEXT NOT NULL
);

CREATE INDEX idx_receitas_nome ON receitas(nome);

CREATE TABLE ingredientes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    receita_id          INTEGER NOT NULL REFERENCES receitas(id) ON DELETE CASCADE,
    nome_ingrediente    TEXT NOT NULL,
    quantidade          INTEGER NOT NULL,
    unidade             TEXT NOT NULL,
    gtin_associado      TEXT
);

CREATE INDEX idx_ingredientes_receita ON ingredientes(receita_id);
CREATE INDEX idx_ingredientes_nome ON ingredientes(nome_ingrediente);
CREATE INDEX idx_ingredientes_gtin ON ingredientes(gtin_associado);
