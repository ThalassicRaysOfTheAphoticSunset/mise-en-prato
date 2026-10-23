-- Suporte a fotos de capa e categorias no módulo de receitas.

ALTER TABLE receitas ADD COLUMN foto_caminho TEXT;
ALTER TABLE receitas ADD COLUMN categoria TEXT;

CREATE INDEX idx_receitas_categoria ON receitas(categoria);
