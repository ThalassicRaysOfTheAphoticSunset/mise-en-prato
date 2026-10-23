-- Produto a granel (cebola, tomate, carnes a peso) não tem GTIN comercial e nunca vai ter.
-- Marcá-los como is_granel = 1 impede que entrem na fila de enriquecimento e queimem
-- a cota da CELEPAR (50 consultas/hora).

ALTER TABLE produto ADD COLUMN is_granel INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_produto_granel ON produto(is_granel);

-- Backfill retroativo baseado nos itens históricos e nas descrições da casa
UPDATE produto SET is_granel = 1
 WHERE id IN (
     SELECT DISTINCT produto_id FROM item
      WHERE UPPER(TRIM(COALESCE(unidade, ''))) IN ('KG', 'GR', 'G', 'QUILO', 'KG.', 'KGS')
         OR (quantidade_milesimos % 1000 != 0
             AND UPPER(TRIM(COALESCE(unidade, ''))) NOT IN ('UN', 'PC', 'CX', 'PCT', 'PT', 'FR', 'L', 'LT', 'ML'))
 )
 OR UPPER(descricao_canonica) GLOB '* KG*'
 OR UPPER(descricao_canonica) GLOB '* GRANEL*'
 OR UPPER(descricao_canonica) GLOB '* A GRANEL*'
 OR UPPER(descricao_canonica) GLOB '* QUILO*';
