-- Suporte a metadados de catálogo e inteligência nutricional da despensa.
-- Preenchidos a partir de dumps locais (Open Food Facts Brasil e CMED/ANVISA).

ALTER TABLE produto ADD COLUMN marca TEXT;
ALTER TABLE produto ADD COLUMN nutriscore TEXT;
ALTER TABLE produto ADD COLUMN nova_group INTEGER;
ALTER TABLE produto ADD COLUMN foto_url TEXT;

CREATE INDEX idx_produto_nova ON produto(nova_group);
CREATE INDEX idx_produto_nutriscore ON produto(nutriscore);
