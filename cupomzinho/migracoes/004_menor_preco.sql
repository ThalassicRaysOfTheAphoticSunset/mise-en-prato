-- O que falta para guardar preço vindo do Menor Preço.
--
-- A API não devolve CNPJ, só um código opaco por estabelecimento. Para loja em
-- que a casa já comprou, dá para parear pelo nome + município e usar o CNPJ do
-- cupom. Para as outras — que são a maioria, e são justamente as que
-- interessam na comparação — não há CNPJ nenhum, e perder o nome da loja
-- tornaria o preço inútil ("R$ 8,49 em algum lugar").
ALTER TABLE preco ADD COLUMN loja TEXT;                 -- nome, quando não há CNPJ
ALTER TABLE preco ADD COLUMN municipio TEXT;
ALTER TABLE preco ADD COLUMN codigo_menor_preco TEXT;   -- o hash opaco da API
ALTER TABLE preco ADD COLUMN distancia_km REAL;

-- Diário de consultas, para o teto por hora sobreviver a reinício do contêiner.
-- Contar em memória seria contar errado justo depois de um Deploy.
CREATE TABLE consulta_menor_preco (
    id          INTEGER PRIMARY KEY,
    feita_em    TEXT NOT NULL,
    alvo        TEXT,           -- termo ou gtin consultado
    confiavel   INTEGER,        -- passou pelo sanidade.py?
    motivos     TEXT
);
CREATE INDEX idx_consulta_quando ON consulta_menor_preco(feita_em);
