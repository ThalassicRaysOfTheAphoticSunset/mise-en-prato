-- Tira o UNIQUE de produto.gtin.
--
-- Dois produtos PRECISAM poder carregar o mesmo código de barras por um tempo:
-- é exatamente o estado em que o enriquecimento pelo Menor Preço descobre que
-- "ENERG MONST 473ML" e "ENERG MONSTER 473ML" são a mesma coisa. Com o UNIQUE,
-- esse momento virava erro de integridade em vez de virar sugestão de
-- unificação — e a regra "mesmo código de barras" na unificacao.py era código
-- que nunca podia rodar.
--
-- Sqlite não remove constraint de coluna: é preciso reconstruir a tabela.
CREATE TABLE produto_novo (
    id                  INTEGER PRIMARY KEY,
    gtin                TEXT,
    ncm                 TEXT,
    descricao_canonica  TEXT NOT NULL,
    apelido             TEXT,
    categoria           TEXT,
    acompanhar          INTEGER NOT NULL DEFAULT 0
);
INSERT INTO produto_novo (id, gtin, ncm, descricao_canonica, apelido, categoria, acompanhar)
    SELECT id, gtin, ncm, descricao_canonica, apelido, categoria, acompanhar FROM produto;
DROP TABLE produto;
ALTER TABLE produto_novo RENAME TO produto;
CREATE INDEX idx_produto_gtin ON produto(gtin);
CREATE INDEX idx_produto_desc ON produto(descricao_canonica);
