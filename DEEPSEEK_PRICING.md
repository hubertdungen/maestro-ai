# DeepSeek no Maestro

Verificado em 2026-11-13 na documentação oficial:
https://api-docs.deepseek.com/quick_start/pricing/
https://api-docs.deepseek.com/guides/thinking_mode/

USD por milhão de tokens; ordem: entrada cache / entrada sem cache / saída.

| Modelo | Pico | Fora de pico |
| --- | --- | --- |
| deepseek-flash (V4.1-Flash-1017) | 0.006 / 0.30 / 1.20 | 0.003 / 0.15 / 0.60 |
| deepseek-v4-pro (V4-Pro-0813) | 0.044 / 1.32 / 3.96 | 0.022 / 0.66 / 1.98 |

Pico oficial: segunda a sexta, 01:00–04:00 e 06:00–10:00 UTC,
exceto feriados chineses. Search e Fetch: 0.75 USD/1000 pedidos bem-sucedidos.
Search/Fetch do CLI passam pelo gateway com reserva de 0.00075 USD por pedido.
Chamadas nativas de ferramentas pagas no modelo e uploads não são expostos;
ferramentas locais do CLI continuam disponíveis. Revalidar preços até 2026-12-12.

Preferência local de encaminhamento (não é um benchmark): Flash para triagem,
leitura, scripts, batch e implementação corrente; esforço baixo por defeito.
Pro para raciocínio/arquitetura difíceis quando a melhoria justificar o custo;
o catálogo permite selecionar Pro via CLI ou API de leitura. O CLI usa Flash
por defeito e o host
pode selecionar Pro com MAESTRO_DEEPSEEK_MODEL=deepseek-v4-pro.
Ambos suportam tools e thinking; Flash suporta visão, Pro não.
Desligar thinking nas respostas curtas sem análise; aumentar esforço apenas
quando necessário. Preservar prefixos estáveis beneficia da cache do fornecedor.

15 EUR por ciclo de dia 20 a dia 20, dividido pelos dias reais desse ciclo.
Exemplo 20 outubro–20 novembro: 15/31 = 0.483871 EUR/dia.
Margem diária adicional de 0.50 EUR: primeiros 0.30 yellowline para necessidade
justificada, últimos 0.20 redline apenas para incidente crítico. Não aumenta
os 15 EUR mensais. Exceções ficam registadas no ledger partilhado.
O executor web admite yellowline para implementação sem subscrição alternativa
com quota e orçamento; redline exige incidente urgente reconhecido pelo host.

CLI e API usam deepseek-budget.json com bloqueio entre processos. Cada chamada
reserva previamente um limite conservador (bytes de entrada + enquadramento,
saída limitada); no fim, acerta pelos tokens reportados. Timeout/resultado
incerto conserva reserva. A contabilização usa pico/sem cache e margem cambial
explícita de 1.10 EUR por USD; é teto conservador, não fatura nem câmbio atual.
Por exemplo, 100k tokens de entrada + 10k saída Flash reservam cerca de
0.0462 EUR nestas condições, antes do enquadramento. A mesma quantidade Pro
representa 0.18876 EUR. Cache e fora de pico reduzem a fatura do fornecedor.

O gateway local mantém a chave real fora do processo CLI e impõe o endpoint
através de overlay de configuração final. O CLI recebe credencial local aleatória.
