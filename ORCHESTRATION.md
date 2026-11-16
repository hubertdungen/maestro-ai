# Coordenação de tarefas do Maestro (12 novembro 2026)

O Maestro gere os agentes: escolhe quem faz o quê e propõe um esboço inicial. Codex tem prioridade nesse papel, com substituição por quota por Antigravity → Claude → DeepSeek. Quota desconhecida não confirma um bloqueio do principal; a fila continua a aplicar os orçamentos a cada execução. Esta hierarquia não define a preferência dos executores nem de quem faz a arquitetura. Numa missão complexa, o gestor pode atribuir uma tarefa `analysis` a um coordenador/arquiteto mais adequado, que pode rever o esboço técnico preservando o objetivo do utilizador. A implementação depende dessa análise e recebe as suas decisões. Esta revisão técnica dentro do plano não altera dinamicamente o DAG persistido.

O servidor mantém a coordenação mesmo quando o motor principal muda. Pedidos simples continuam com uma execução. Pedidos amplos de investigação e melhoria, pedidos explícitos de delegação e tarefas classificadas como complexas passam pelo coordenador: uma chamada produz um plano JSON com até seis tarefas, instruções, motores/modelos/esforços e dependências. O plano é validado antes da execução; JSON inválido usa pesquisa → implementação → revisão como reserva.

`maestro_workflow.py` executa o DAG. Até quatro tarefas independentes de leitura podem correr em paralelo, partilhando o limite global com os outros pedidos da fila; uma tarefa de escrita executa isolada na missão. O número de agentes segue o trabalho útil e as dependências: uma cadeia continua sequencial e um pedido simples conserva um motor. O planeador deve poupar tokens com pesquisa económica e contexto limitado, evitar duplicação e preservar o especialista necessário. Esperar por dependências é trabalho do Python, sem chamadas ao modelo. O coordenador não mantém uma conversa ativa à espera. `maestro_runtime.py` passa apenas os resultados dos pré-requisitos, com tamanho limitado, e mantém o histórico completo no disco. O resultado do utilizador vem das tarefas de entrega; as passagens restantes ficam nos cartões.

## Capacidades e modelos

`maestro_models.py` descobre o catálogo Codex da conta, os aliases do CLI Claude, os IDs devolvidos por `agy models` e os modelos anunciados pela API DeepSeek. O menu «… → Modelos e esforço» mostra fonte, disponibilidade e restrições. O catálogo confirma opções do transporte instalado; não prova acesso efetivo a todos os modelos de outra app. A quota continua a ser verificada na admissão da fila.

Neste NAS, o teste sem inferência do sandbox Codex de leitura falhou com restrições do bubblewrap. O catálogo assinala essa capacidade como indisponível. O Codex mantém o executor de implementação existente. Não se remove a restrição de leitura para fingir uma pesquisa segura.

`maestro_readonly.py` oferece pesquisa DeepSeek por API com apenas três ferramentas: listar pastas, ler excertos e pesquisar texto. Não expõe shell, escrita, MCP ou URLs livres. O acesso fica limitado à pasta do projeto e às raízes/ficheiros concedidos pelo servidor. Os scouts do Maestro recebem também a pasta dos scripts Maestro. Symlinks, traversal e nomes de credenciais são recusados. Há limites de chamadas, tempo e tamanho. O perfil DeepSeek CLI continua separado, para implementação; os modelos API do menu estão identificados como apenas leitura.

Imagem anexada exige um motor com visão. Atualmente, quando não há um executor de leitura com visão e quota, o pedido com imagens conserva uma execução única; o DeepSeek textual não recebe capacidade de visão fictícia.

## Persistência, falhas e retoma

Os planos e resultados ficam em `~/.local/state/maestro/workflows/<turn_id>.json`, com escrita atómica. Uma tarefa é gravada como em execução antes de lançar o agente. Uma falha bloqueia as dependentes e preserva resultados parciais; o turno fica interrompido na fila. Depois de reiniciar, tarefas incertas exigem «Retomar» na fila. Essa ação conserva tarefas concluídas e repete apenas as que falharam/interromperam, libertando as dependentes. Uma escrita interrompida pode ter produzido alterações parciais; a instrução de retoma deve inspecionar o estado antes de continuar.

Parar cancela tarefas pendentes, marca as que estavam em execução e termina os grupos de subprocessos. Uma chamada HTTP DeepSeek já em curso acaba ou atinge o timeout antes de observar o cancelamento; não lança outra ronda após esse sinal. O hot reload espera que os turnos ativos terminem. A passagem para o próximo orquestrador aponta para o plano persistido.

## Verificação

- `python3 -m unittest discover -s /home/hubert/scripts/maestro/tests -q`
- Verificação HTTP do bundle anterior executada contra o módulo de produção novo: 56 verificações passaram.
- Chromium móvel: seleção de modelo/esforço, payload do compositor, cartões/dependências, passagem aberta preservada no polling, teclado, ausência de erros JS e de overflow horizontal.
- Teste real em `/tmp/maestro-real-workflow-evl_3t6f`: planeamento e pesquisa DeepSeek Flash → implementação Codex Luna → revisão DeepSeek Flash. O ficheiro final foi confirmado pelo processo de teste como `12\n`; o resultado e o DAG ficam nessa pasta temporária. O primeiro teste revelou o sandbox indisponível e motivou o adaptador de leitura.

Limites atuais: o plano é definido no início; não há replaneamento dinâmico ilimitado nem distribuição entre máquinas. Ações de deploy continuam a usar o fluxo «Autorizar e avançar» existente quando exigem aprovação específica.

## Política por modelo e reservas (12 novembro, revisão da Frota)

`maestro_model_policy.py` define preferências locais por modelo, expostas no catálogo, na Frota e no contexto do planeador. São regras operacionais, não benchmarks: Luna/Haiku/Flash para trabalho curto; Sol/Sonnet/Argon para implementação corrente; Terra/Astra/Opus/Hydrogen para análise ou código exigentes. Fable e GPT-OSS ficam como reserva sem especialidade presumida. IDs antigos continuam como alternativas anunciadas pelo CLI. O esforço automático é low, medium ou high conforme a tarefa e apenas se anunciado. Max/ultra continuam disponíveis para seleção explícita ou pelo planeador, sem os impor por defeito. A proteção de ritmo do Opus impede escolha automática acima dos limiares existentes.

O classificador passa também a atribuir o executor realmente recomendado: web → Sonnet, NAS/modding → Codex, Unity → Opus quando a complexidade e o ritmo o permitem, senão Sonnet/Codex; batch textual → DeepSeek. Antes, essas especialidades apareciam na descrição dos agentes mas o executor permanecia frequentemente no gestor da hierarquia. A hierarquia do Maestro fica independente destas preferências de execução.

A execução sem modelo explícito escolhe agora um ID do catálogo em vez de presumir sempre Sol. O planeador recebe finalidade, grupo de quota, visão e restrição de leitura. DeepSeek API fica limitado a leitura; DeepSeek CLI não recebe imagens. Só IDs Gemini do transporte AGY recebem capacidade de imagem automática; não se infere visão dos seus modelos 3P.

Há no máximo três tentativas, registadas no resultado persistido. Uma rejeição explícita do ID pode tentar outro modelo. Uma rejeição de quota exclui o grupo de conta inteiro: trocar Sol por Luna não renova Plus, trocar Sonnet por Opus não renova Max e CLI/API DeepSeek partilham saldo. Seleção manual de modelo é preservada e não tem substituição silenciosa. Resultados parciais, erros de build, cancelamentos, timeouts e sinais de ferramentas executadas não originam repetição automática. Claude/DeepSeek CLI sem streaming de ferramentas não permitem provar que uma falha de quota de escrita ocorreu antes de alterações; nesses casos conserva-se a falha para retoma. DeepSeek API pode trocar após HTTP 402/429/404 na primeira ronda, antes de ferramentas. AGY com step_update conserva a falha para inspeção. O bloqueio AGY existente continua conservador para todos os seus IDs; quotas 3P separadas não constituem acesso confirmado.

Auditoria do histórico disponível nesta revisão: 39 registos Codex Sol e 17 DeepSeek (7 Maestro CLI, 10 motor CLI). Isto não prova execução de todos os IDs listados. Os testes de adaptador usam processos simulados para verificar argumentos e reservas sem gastar tokens de todos os fornecedores. Testes reais de acesso por modelo não foram feitos nesta revisão.

A Frota usa listas expansíveis com finalidade e grupo de quota, sem scroll interno por lista; preserva abertura e scroll do painel no polling. O badge «só leitura» identifica apenas adaptadores exclusivamente de leitura, não o suporte opcional a esse modo em Claude/Codex.

Validação desta revisão: 102 testes passaram; os adaptadores dos 32 IDs descobertos e 84 combinações CLI de modelo/esforço passaram com processos/respostas simulados (zero pedidos de inferência). Chromium a 1440px e 390px confirmou ausência de overflow horizontal e scroll interno das listas, rótulos corretos e preservação de abertura/scroll no polling. Evidência local em `/tmp/maestro-model-contract-audit.json` e `/tmp/maestro-model-audit-{1440,390}.png`. O servidor carrega as alterações pelo hot reload após terminar todos os turnos ativos.


## Concorrência por subscrição e orçamento DeepSeek (12 novembro, revisão das 21h)

Uma execução por grupo de quota, até quatro fornecedores em simultâneo. O bloqueio de escrita global foi substituído por conflitos de caminhos (incluindo pastas ascendentes/descendentes): projetos independentes podem escrever em paralelo com fornecedores diferentes. A escrita continua isolada dentro da missão. Reservas adquirem a vaga do fornecedor efetivamente escolhido em cada tentativa. Gemini, Codex e Claude podem desempenhar a mesma especialidade em tarefas independentes; isto não justifica repetir toda a tarefa. A fila favorece subscrições abaixo do ritmo semanal proporcional quando a telemetria permite calculá-lo, antes de pagar API, sem substituir um modelo complexo por um scout. Sem um alvo conhecido no AGY, não se presume margem diária.

`maestro_budget.py` aplica 15 EUR por ciclo [dia 20, próximo dia 20), Europe/Lisbon, e 15/dias-do-ciclo por dia, sem acumular verba diária não usada. A contagem inicia na ativação: não reconstrói consumo anterior. JSON persistente e flock entre processos reservam custo antes de cada chamada de completions, incluindo o melhorador de pedidos. Reserva por bytes de entrada mais margem, saída limitada e preços de pico/cache miss, com contabilização protetora de 1 USD = 1,10 EUR (não é câmbio de mercado). A resposta com usage permite libertar reserva excedente; falhas incertas mantêm a reserva. Preços conhecidos consultados em https://api-docs.deepseek.com/quick_start/pricing/ em 12/11/2026, revisão obrigatória até 12/12/2026; modelos desconhecidos ou preços expirados bloqueiam chamadas. A interface mostra valores contabilizados/reservados, que podem superar o custo real.

O CLI dsh não anuncia teto de custo. Fica indisponível no catálogo e bloqueado nas entradas de execução/delegação/substituição do Maestro; a API de pesquisa continua disponível e a implementação recorre às subscrições. Este teto controla chamadas encaminhadas pelo Maestro; ferramentas e clientes usados diretamente fora dele não estão abrangidos. Não há carregamentos automáticos de saldo.


## Pedidos mistos e sugestões de implementação

A deteção local de pergunta seguida de alteração ativa o planeamento, sem chamar um modelo para classificar cada mensagem. O planeador prefere um modelo económico com quota e ferramentas compatíveis. Decide se separa explicação (`analysis`, só leitura) e implementação, ou mantém uma tarefa quando a divisão acrescentaria trabalho. As janelas de quota e as capacidades do catálogo continuam a limitar todas as escolhas.

O planeador recebe contexto recente limitado. Cada executor recebe a instrução da sua subtarefa, âmbito de escrita e resultados das dependências. O contexto original fica em `workflows/<turn_id>.context.txt` (permissões 0600), disponível por leitura quando forem necessários detalhes anteriores ou referências a anexos. Não se presume poupança total de tokens: a telemetria mede apenas as chamadas efetivas e os estimadores existentes.

Respostas que contenham sugestões de implementação reconhecidas pela interface apresentam **Aplicar implementação sugerida**. O clique envia a proposta exata à fila da mesma conversa, mantendo projeto/modelo e preservando o rascunho. Um identificador estável evita duplicados; a conversa e a fila permitem reconstruir o estado do botão após atualização. A deteção textual cobre sugestões PT/EN comuns, não é um classificador semântico universal. Conversas arquivadas não oferecem esta ação.

Os novos chats recebem um título curto após o primeiro pedido, refinado com a primeira resposta ao mesmo pedido. A geração corre em background com DeepSeek Flash, raciocínio desativado, saída limitada a 48 tokens e orçamento partilhado. Envia apenas até 1400 caracteres do pedido e 1000 da resposta; não envia o histórico nem lê anexos. Sem API/orçamento usa um título local. Não repete a geração nos turnos seguintes, não altera nomes explícitos e não recria chats apagados. A lista recebe o nome pelo polling existente.


## Orçamento diário Codex Plus

A admissão usa 100/7 = 14,2857 pontos percentuais da quota semanal por dia de 24h, ancorado no reset semanal reportado. O teto acumulado cresce uma parcela por dia; a quota não usada pode acumular, mas o consumo diário observado também está limitado a 100/7. As observações persistem em `codex-daily-usage.json`, com lock entre processos. A primeira leitura tardia é identificada como parcial; não inventa histórico de consumo. Esta política usa percentagens do fornecedor, não uma quantidade garantida de tokens.

Ao atingir qualquer teto, a fila escolhe uma alternativa compatível ou espera com `daily_budget`. Escolha manual, botão Avançar, imagens e complexidade isolada não dispensam o orçamento. Telemetria semanal ausente/expirada bloqueia a admissão normal. A delegação CLI, a hierarquia de substituição, os workflows e as reservas de modelo respeitam a proteção.

A única exceção automática local (`essential_reserve`) exige uma intervenção urgente explícita sobre um incidente crítico (perda/corrupção de dados ou indisponibilidade de produção), classificação avançada e ausência de outro fornecedor compatível com quota normal. O motivo fica na admissão visível. É uma heurística conservadora, não uma avaliação universal de qualidade. O limite real do fornecedor continua obrigatório. Trocar entre modelos Codex não cria orçamento novo.

O controlo aplica-se antes de iniciar cada chamada; uma chamada em curso termina para preservar escritas. Leituras do fornecedor têm cache até 90s e resolução percentual: este mecanismo não garante um corte exato por token. O hot reload aguarda os turnos ativos terminarem.


## Funções estáveis e tags

As funções não são nomes de modelos nem sessões adicionais. `Maestro` coordena;
`Architect` identifica a fase de planeamento do coordenador; `Sentinel` faz a triagem
da fila; `Scout` investiga; `Analyst` analisa; `Maker` implementa; `Auditor` revê;
`Scribe` sintetiza. A árvore mostra a função real de cada nó ao lado do motor.

Sentinel tenta Haiku (timeout de 20 segundos), depois DeepSeek Flash pelo adaptador
API com o orçamento partilhado. Só aceita as respostas exatas `PARALELO` e `ESPERAR`.
Uma decisão válida de esperar não provoca nova tentativa noutro motor. Se ambos
falharem, a fila espera localmente e volta a tentar noutra passagem; erros não ficam
em cache. A cache inclui o texto para invalidar decisões após editar um pedido.
O catálogo de funções está em «Funções e tags» na Tabela do Maestro. Os títulos de
conversas continuam a ter a sua alternativa local existente.


## Entrega fechada no mesmo turno (16 novembro, revisão de arquitetura)

Revisão feita sobre 8 conversas, 476 execuções, 299 turnos e 36 planos. O que os dados mostraram e o que mudou:

| Evidência | Causa no código | Alteração |
|---|---|---|
| 25 de 36 planos entregavam como resposta o texto de um revisor só de leitura; 18 de 26 revisões declaravam não ter shell | o nó terminal do DAG era a revisão; os executores dos planos não recebiam as regras de entrega | os achados da revisão voltam ao implementador **dentro do mesmo turno** (nó `entrega`, acrescentado ao plano persistido); um pedido de alteração nunca termina num plano só de leitura; todos os nós recebem o pedido original e os de escrita o contrato de entrega |
| os 5 turnos com plano que entraram na porta de qualidade esgotaram 3/3 ciclos | a recusa devolvia o turno à fila, que reabria o plano já concluído e devolvia o mesmo texto | `quality_gate` corre a correção no próprio trabalho, com um motor de escrita e o prompt completo; o turno não volta à fila |
| 71 de 304 respostas acabavam a pedir autorização; 167 admitiam não ter visto o resultado; 88 mandavam fazer Ctrl+F5 | o prompt convidava a propor comandos para «Autorizar e avançar» e não havia forma de ver uma página | `maestro_qagate.CONTRACT` (um só texto para turnos, planos e correções) e `tools/ver.py` (captura PNG + texto no DOM renderizado, desktop ou telemóvel) |
| 55 turnos admitidos como `parallel_write`, mas 98 % do tempo com um só worker | depois de admitido, o turno ficava preso em `ExecutionSlots` atrás da pasta partilhada (`~`) | o turno admitido em paralelo recebe âmbito próprio (`exec_scope`); irmãos do mesmo plano continuam com escrita exclusiva; o Sentinel deixa de tratar «mesmo disco» como conflito |
| 44 falhas «orçamento diário/mensal insuficiente»; 11 planeamentos falhados num dia | DeepSeek contava como disponível com a verba do dia quase esgotada | `available` exige 10 % de folga diária; sem verba normal sai do planeamento, dos scouts e das escolhas automáticas |
| «Então eu não te pedi há pouco…?» | só 3 pedidos antigos entravam no prompt; respostas cortadas pelo início | registo de uma linha para todos os pedidos anteriores; as respostas recentes mantêm o início e o fim |

Revisores e scouts em Claude passam a ter Bash limitado a inspeção: `git status|diff|log|show` e `tools/ver.py`. Verificado com uma chamada real: escrita direta e escrita encadeada atrás de um prefixo permitido são negadas pelo modo `dontAsk`.

Limites conhecidos: a porta de qualidade continua a ler texto (regex PT), não o estado real do repositório; o classificador de domínio/dimensão continua por palavras-chave e contagem de palavras; pedidos seguidos no mesmo chat sobre a mesma app continuam em série, por desenho.

### Segunda ronda (16 novembro)

- **Porta de qualidade com estado real.** `maestro_qagate.repo_findings` olha para os repositórios em que o turno mexeu (pasta do projeto e caminhos citados na resposta): ficheiros de código alterados depois do início do turno e por commitar, e commits do turno por enviar. Trabalho que já lá estava não é imputado ao turno; bases de dados, logs e estado de apps ficam de fora; «não faças commit» no pedido desliga a verificação. Substitui o limite «a porta só lê texto».
- **Promessas sem prova recusadas:** «faz Ctrl+F5», «limpa a cache», «não vi num browser».
- **Retoma após interrupção:** um turno reposto na fila por reinício recebe a nota para ver primeiro o estado real. **Quota:** um motor que recusa por quota/orçamento não é tentado segunda vez. **Processos:** o timeout mata o grupo inteiro do motor.
- **Aviso de login do Claude:** `claude auth status` (local, sem tokens, cache de 1 h) dá a data de fim da sessão; a 10 dias do fim o aviso aparece no painel de pensamentos e em `maestro.py --status`.
- **Chat grande, medido num browser real** (308 mensagens): abrir descarregava 149 imagens (34 MB) e o polling trazia o JSON da conversa (1,1 MB) a cada segundo durante um trabalho. Agora as imagens carregam só à vista, com altura reservada e cache (`ETag`), e a conversa só é reenviada quando muda (`_v`). Imagens na abertura: 34 MB → 0–2 MB; bloqueio do browser: 1,2–2,0 s → 0,8–0,9 s. `content-visibility` foi medido e rejeitado (sem ganho, mais saltos).

### Terceira ronda (16 novembro): conversa como contexto, janela de mensagens

- **O classificador conhece a conversa** (`thread_context`). Antes, cada mensagem era classificada sozinha: «continua igualzinho no meu» ou «Logo bonito e bem feito» a meio de um trabalho eram «Rápida / Direta» e iam para DeepSeek Flash/Haiku. Agora, durante 6 h após o último turno: uma resposta curta herda o domínio e fica pelo menos em «Média»; uma mensagem que diz que a tentativa anterior falhou (`says_it_failed`: «continua igual», «ainda não está», maiúsculas, etc.) passa a «Exigente» e vai para um só motor mais forte (Opus depois de Sonnet/Haiku, Codex depois de Opus), sem plano multiagente. A proteção de ritmo do Opus mantém-se. Repetido sobre os 285 pedidos do histórico: 93 deixam o modelo mais barato e 35 sobem de modelo.
- **Porta de qualidade:** «não fiz commit nem PR», «por commitar» e afins são recusados (salvo pedido para ficar local); os repositórios da pasta pessoal também são verificados, mas aí só contam ficheiros que a própria resposta nomeia. Caso real que motivou: um turno alterou `ui/app.css` em produção e respondeu «Não fiz commit nem PR».
- **Janela de mensagens no chat:** uma conversa abre com as últimas 60 mensagens (mais as que ainda esperam por ti); as anteriores entram ao subir ou pelo botão «↑ Mensagens anteriores (N)». Nada é eliminado e a posição guardada conta a partir do fim. Medido no chat de 314 mensagens: bloqueio do browser ao abrir 0,8–1,2 s → 0 ms; subir até à primeira mensagem sem saltos (desvio máximo 2 px); regresso à mesma mensagem ao trocar de chat. As imagens têm moldura fixa (260×180) porque a largura desconhecida mudava a quebra de linha ao carregar (+188 px medidos).
- **Largura de leitura com a Estratégia aberta:** quando a coluna de leitura ficaria abaixo de 480 px, o painel direito recolhe-se sozinho e volta ao fechar a Estratégia; reaberto à mão, fica como o utilizador o deixou até a Estratégia fechar. Medido: balão de resposta 166 → 526 px a 1280 e 326 → 686 px a 1440; a 1920 nada muda.

### Prova e medição (16 novembro)

- **Motivos da porta registados** (`qa_reasons` na fila e na mensagem, e no cartão ao vivo): cada recusa diz porquê, em linguagem simples.
- **Estado em disco com escrita atómica** (`_save_json`, temporário único por processo e thread): servidor, cron e agentes a correr `--status` escreviam os mesmos ficheiros com `write_text`; o sinal de quota do Gemini ficou com JSON inválido e o painel mostrava «0 % usado» num motor bloqueado.
- **`tools/saude.py`**: mede nos dados reais os indicadores que motivaram a revisão (respostas a pedir autorização, sem verificação, git por fechar, pedidos a dizer que falhou, espera na fila, falhas por motor, motivos da porta) e compara com o período anterior. `--desde "AAAA-MM-DD HH:MM"` mede desde um deploy.
- **Realidade acima das palavras:** se os repositórios em que o turno mexeu estão com commit e push feitos, uma frase como «não commitei o `__pycache__`» deixa de provocar nova passagem. Sem repositório para conferir, as palavras continuam a contar.
- **Prova real (16/11, repositórios descartáveis, pelo Maestro em produção):** pedido simples em Haiku, plano multiagente (implementação Sonnet → revisão Haiku → nó `entrega`) e dois pedidos em simultâneo (27–41 s de sobreposição). Nos cinco turnos o executor deixou ficheiros por commitar à primeira; a porta detetou-o no git e o resultado final ficou com commit e push em todos.
