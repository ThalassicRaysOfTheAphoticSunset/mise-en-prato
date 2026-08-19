-- Esquema inicial do cupomzinho.
--
-- Dinheiro é INTEGER em CENTAVOS e quantidade é INTEGER em MILÉSIMOS. Não há
-- float em lugar nenhum: soma de gasto tem de fechar no centavo, e o cupom
-- traz peso com três casas ("0,256 KG"). A conversão mora em banco.py.

CREATE TABLE estabelecimento (
    cnpj                TEXT PRIMARY KEY,           -- 14 dígitos, sem pontuação
    razao               TEXT NOT NULL,
    fantasia            TEXT,
    logradouro          TEXT, numero TEXT, complemento TEXT,
    bairro              TEXT, municipio TEXT, uf TEXT,
    -- A API do Menor Preço NÃO devolve CNPJ, só um código opaco. Este é o
    -- pareamento entre os dois mundos: resolvido uma vez por loja, na mão se
    -- preciso, e vale para sempre.
    codigo_menor_preco  TEXT
);

CREATE TABLE produto (
    id                  INTEGER PRIMARY KEY,
    gtin                TEXT UNIQUE,                -- só chega pelo Menor Preço; o cupom não traz
    ncm                 TEXT,
    descricao_canonica  TEXT NOT NULL,
    apelido             TEXT,                       -- o nome que a casa usa
    categoria           TEXT,
    acompanhar          INTEGER NOT NULL DEFAULT 0  -- entra no vigia de preços?
);

CREATE TABLE cupom (
    chave           TEXT PRIMARY KEY,               -- 44 dígitos: reenviar é idempotente
    cnpj            TEXT NOT NULL REFERENCES estabelecimento(cnpj),
    emitido_em      TEXT,                           -- ISO-8601
    numero          TEXT, serie TEXT, protocolo TEXT,
    total_centavos      INTEGER,
    desconto_centavos   INTEGER NOT NULL DEFAULT 0,
    pago_centavos       INTEGER,
    forma_pagamento TEXT,
    consumidor      TEXT,
    morador         TEXT,                           -- quem enviou (whois do tailscaled)
    origem          TEXT NOT NULL DEFAULT 'qrcode', -- qrcode | foto | manual
    completo        INTEGER NOT NULL DEFAULT 1,
    avisos          TEXT,                           -- por que caiu na fila de revisão
    url_qr          TEXT,                           -- sem ela não dá para reconsultar
    criado_em       TEXT NOT NULL
);
CREATE INDEX idx_cupom_emitido ON cupom(emitido_em);
CREATE INDEX idx_cupom_morador ON cupom(morador);

CREATE TABLE item (
    id              INTEGER PRIMARY KEY,
    chave           TEXT NOT NULL REFERENCES cupom(chave) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,               -- ordem no papel; linha repetida é preservada
    descricao       TEXT NOT NULL,
    codigo_loja     TEXT,                           -- "(Código: 456641)" — interno da loja, NÃO é GTIN
    unidade         TEXT,
    quantidade_milesimos INTEGER NOT NULL,
    unitario_centavos    INTEGER NOT NULL,
    total_centavos       INTEGER NOT NULL,
    produto_id      INTEGER REFERENCES produto(id),
    UNIQUE (chave, seq)
);
CREATE INDEX idx_item_produto ON item(produto_id);

-- (loja, texto que a loja imprime) -> produto. É o degrau que faz o segundo
-- cupom da mesma loja ser instantâneo, sem consultar nada.
CREATE TABLE apelido_loja (
    cnpj            TEXT NOT NULL REFERENCES estabelecimento(cnpj),
    descricao_norm  TEXT NOT NULL,                  -- já normalizada
    produto_id      INTEGER NOT NULL REFERENCES produto(id),
    PRIMARY KEY (cnpj, descricao_norm)
);

-- Série única de preço, alimentada pelos dois lados. É ela que responde
-- "paguei caro?" e "quem está mais barato agora?".
CREATE TABLE preco (
    id              INTEGER PRIMARY KEY,
    produto_id      INTEGER NOT NULL REFERENCES produto(id),
    cnpj            TEXT REFERENCES estabelecimento(cnpj),  -- nulo se só do Menor Preço
    unitario_centavos INTEGER NOT NULL,
    observado_em    TEXT NOT NULL,
    fonte           TEXT NOT NULL                   -- cupom | menor_preco
);
CREATE INDEX idx_preco_produto ON preco(produto_id, observado_em);
CREATE UNIQUE INDEX idx_preco_unico ON preco(produto_id, cnpj, unitario_centavos, observado_em, fonte);

CREATE TABLE lista (
    id          INTEGER PRIMARY KEY,
    nome        TEXT NOT NULL,
    morador     TEXT,
    criada_em   TEXT NOT NULL,
    fechada_em  TEXT
);
CREATE TABLE lista_item (
    id          INTEGER PRIMARY KEY,
    lista_id    INTEGER NOT NULL REFERENCES lista(id) ON DELETE CASCADE,
    produto_id  INTEGER NOT NULL REFERENCES produto(id),
    quantidade_milesimos INTEGER NOT NULL DEFAULT 1000,
    UNIQUE (lista_id, produto_id)
);
