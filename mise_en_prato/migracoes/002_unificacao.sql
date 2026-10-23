-- Memória das unificações RECUSADAS.
--
-- Unificar apaga um dos produtos, então par unificado nunca reaparece. Par
-- recusado reapareceria toda vez, e uma sugestão que você já disse "não" três
-- vezes deixa de ser sugestão e vira ruído. Guarda-se pela descrição
-- normalizada, e não pelo id, justamente porque o id some numa unificação.
CREATE TABLE unificacao_recusada (
    desc_a TEXT NOT NULL,
    desc_b TEXT NOT NULL,
    PRIMARY KEY (desc_a, desc_b)     -- sempre gravado com desc_a < desc_b
);
