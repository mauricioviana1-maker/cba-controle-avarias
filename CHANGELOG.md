# Registro de alterações — CBA 3.0 (pacote de testes)

## Novas funções de gestão

- Pareto com valor por causa e porcentagem acumulada.
- Identificação de reincidência por SKU, setor e causa.
- Comparativo entre períodos de igual duração (7 a 365 dias).
- Ações corretivas 5W2H ligadas aos protocolos de avaria.
- Políticas de SLA por centro de distribuição e gravidade.
- Geração manual de relatórios CSV e rotinas automáticas opcionais.

## Nova mobilidade

- QR Code com endereço de abertura da ocorrência sob autenticação.
- Offline experimental no navegador, com fila IndexedDB e sincronização sem fotos.

## Inteligência experimental

- Previsão de perdas por médias semanais e desvio-padrão como faixa ilustrativa (sem IA treinada).
- Assistente de consulta gerencial baseado em regras (sem modelo generativo).

## Segurança e esquema

- Índice único para evitar duplicação de lançamentos offline.
- Tabelas aditivas para políticas SLA, ações 5W2H e relatórios.
- Secret opcional `REPORT_JOB_TOKEN` para proteger execução de tarefas.
- Menus novos visíveis por perfil e testes automatizados expandidos.

## Limitações conhecidas

- É obrigatório testar a migração com cópia do banco antes de publicar.
- Modo offline sem criptografia local nem fotos: somente dados fictícios.
- Histórico analisado tem limite de 20 mil linhas por janela e não deve substituir relatórios corporativos completos.
- Relatórios automáticos necessitam agendamento no GitHub Actions e segredo correspondente no Render.
- As funções de previsão e assistente são analíticas experimentais, não IA treinada nem LLM.
