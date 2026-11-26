# Resolução semanal do Pulso

`maestro_pulse_weekly.py` é chamado pelo cron a cada minuto. Ao sábado às
09:00 em Europe/Lisbon submete uma revisão à fila real do Maestro (:8109),
num chat do projeto NAS, sem precisar do browser aberto. Não usa o antigo
agendador `maestro_prompt`, que apenas classificava e registava contexto.

A revisão inclui alertas atuais, incidentes encerrados nos últimos sete dias
e falhas de recolha. O agente confirma o estado antes de corrigir e valida
as alterações reversíveis. Não autoriza apagar dados pessoais, gastar dinheiro,
enviar mensagens a terceiros nem reiniciar maestro-web. O modelo Codex é
fixado a partir do catálogo local para evitar fallback para fornecedores pagos.
A falta de quota ou do serviço mantém o pedido para nova tentativa.
A pausa global da fila continua a ser respeitada.

O chat chama-se `Pulso · resolução semanal · AAAA-MM-DD · temporário 48h`.
48 horas depois da criação é arquivado e eliminado pela API. Se ainda houver
uma execução ativa, aguarda a conclusão e tenta novamente no minuto seguinte.
Só são eliminados IDs registados por esta rotina; as conversas existentes
não são abrangidas. Os registos técnicos normais da fila/execução mantêm-se.

A instalação não executa retroativamente a semana anterior. Uma execução
perdida por indisponibilidade é recuperada até ao sábado seguinte. As chamadas
à fila têm um request_id estável, impedindo duplicação após uma resposta perdida.
Não se força a execução de pedidos nem se ultrapassam pausas/quota.

Estado: `~/.local/state/maestro/pulse-weekly.json` (600). Para suspender novas
revisões, definir `enabled` como `false`; a expiração continua ativa.

- Instalar estado: `python3 maestro_pulse_weekly.py --install`
- Consultar: `python3 maestro_pulse_weekly.py --status`
- Ver entrada sem executar agente: `python3 maestro_pulse_weekly.py --preview`
- Tick: `python3 maestro_pulse_weekly.py`
- Testes isolados: `python3 -m unittest discover -s tests -p test_pulse_weekly.py -v`

Cron instalado no NAS (o calendário é calculado pelo Python com timezone
explícita, independentemente do timezone do cron):

```cron
* * * * * /usr/bin/python3 /home/hubert/scripts/maestro/maestro_pulse_weekly.py >> /home/hubert/scripts/logs/pulse-weekly.log 2>&1
```

## Resumo das correções no email

O relatório do Pulso mantém-se ao domingo às 09:00, depois da revisão de sábado
às 09:00. Inclui uma secção «Resolução automática do NAS», tanto no resumo
semanal como no mensal que o substitui no último domingo. O resultado final,
com diagnóstico, correções e validação, é guardado no estado da revisão antes
da eliminação do chat. O bloco técnico `pulse-action` fica fora do email.

Se a revisão ainda aguardar quota/fila ou conclusão, o relatório indica esse
estado sem anunciar correções. Quando terminar depois de um relatório já enviado,
o verificador envia o resultado por email. Uma ação indispensável do Hubert
continua a gerar um aviso imediato, incluindo também o resumo da revisão.
Falhas de SMTP preservam o chat e são repetidas no próximo minuto.

Instalar/atualizar a integração no Pulso, depois de publicar os dois módulos:

```sh
python3 maestro_pulse_report.py --install /home/hubert/scripts/pulse/notify.py
```

A instalação é idempotente e não altera o horário nem as definições SMTP.
