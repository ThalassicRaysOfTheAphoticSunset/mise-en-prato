-- Lembrar de quem já foi perguntado.
--
-- Produto a granel (CEBOLA NACIONAL KG) não tem código de barras e nunca vai
-- ter. Sem esta memória, ele volta ao topo da fila a cada rodada e queima uma
-- consulta do teto por hora para receber sempre a mesma resposta. Quem nunca
-- foi perguntado passa na frente de quem já foi.
ALTER TABLE produto ADD COLUMN gtin_buscado_em TEXT;
