-- Conserta o índice que impedia preço duplicado — e não impedia.
--
-- O índice antigo era (produto_id, cnpj, unitario, observado_em, fonte). Em
-- sqlite dois NULL nunca são iguais num índice único, então linha com cnpj
-- nulo NUNCA colidia: e cnpj é nulo justamente para as lojas do Menor Preço em
-- que a casa nunca comprou, que são a maioria. Na prática, repetir a mesma
-- consulta duplicava quase tudo.
--
-- A identidade da loja passa a ser o primeiro campo que existir: cnpj (quando
-- pareamos com um cupom), senão o código opaco da API, senão o nome.

-- primeiro, apaga as duplicatas que o índice furado deixou entrar
DELETE FROM preco WHERE id NOT IN (
    SELECT MIN(id) FROM preco
     GROUP BY produto_id, COALESCE(cnpj, codigo_menor_preco, loja, ''),
              unitario_centavos, COALESCE(observado_em, ''), fonte
);

DROP INDEX IF EXISTS idx_preco_unico;
CREATE UNIQUE INDEX idx_preco_unico ON preco(
    produto_id,
    COALESCE(cnpj, codigo_menor_preco, loja, ''),
    unitario_centavos,
    COALESCE(observado_em, ''),
    fonte
);
