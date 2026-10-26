# CBA 3.0 — Controle de Avarias (piloto)

Atualização experimental do CBA 2.0 com dez melhorias priorizadas pelo solicitante. **Não homologado para operação corporativa ou dados reais.**

## Recursos deste pacote

1. Pareto de perdas por causa, com percentual individual e acumulado, limitado a 20.000 registros por período.
2. Detecção de reincidência por SKU + setor + causa nos períodos consultados.
3. Comparativo entre janelas consecutivas de 7, 30, 90, 180 ou 365 dias.
4. Planos de ação 5W2H, com responsável, prazo, custo e situação.
5. Relatórios CSV cadastrados com frequência diária/semanal/mensal; geração manual imediata. Agendamento remoto é **opcional** e exige dois segredos iguais em Render/GitHub.
6. Registro offline **experimental sem fotos**, com IndexedDB, fila de pendências e sincronização manual, somente após login anterior no dispositivo, para dados fictícios.
7. QR Code para abrir ocorrência, preservando autenticação e regras de acesso.
8. SLAs ajustáveis por gravidade/CD, com cálculo em horas corridas desde a abertura.
9. Projeção **estatística experimental** das perdas da próxima semana, exigindo 8 semanas completas. **Não é IA treinada**, nem faixa de confiança.
10. Assistente gerencial **baseado em regras**, com perguntas predefinidas e consultas ao banco conforme permissões. **Não é chatbot de IA generativa.**

## Links do projeto

- GitHub: https://github.com/mauricioviana1-maker/cba-controle-avarias
- Render: https://dashboard.render.com/web/srv-db8h902jnfac73fp69ng
- PostgreSQL: https://dashboard.render.com/d/dpg-db8h8bei0phs73fmcre0-a
- Aplicativo hospedado (atualização não concluída): https://cba-controle-avarias.onrender.com

## Como instalar manualmente

Leia primeiro o arquivo **PASSO_A_PASSO_IMPLANTACAO.md**. Não envie o ZIP inteiro ao GitHub como único arquivo. Primeiro crie uma branch a partir de `main`. Faça backup e ensaie a migração antes de qualquer merge.

## Banco e migração

A atualização cria as tabelas `action_plan`, `sla_policy`, `report_schedule` e `report_run`, adiciona `offline_key` em `damage` e cria um índice único desse identificador. O aplicativo tenta executar a migração aditiva quando inicializa. Uma falha de migração poderá impedir o boot; **backup e teste em cópia são obrigatórios** antes de usar o banco existente. Uma versão posterior deveria usar Alembic e scripts de rollback controlados.

O PostgreSQL gratuito atual está previsto para expirar em 14 de novembro de 2026; confira o painel Render, obtenha backup e considere plano persistente antes de armazenar qualquer dado importante.

## Dependências / ambiente

- Python 3.12; Flask e SQLAlchemy, driver PostgreSQL, Pillow e `qrcode[pil]`.
- `DATABASE_URL`, `SECRET_KEY` e `ADMIN_PASSWORD` permanecem configuradas como no CBA 2.0. Nunca envie essas variáveis ao GitHub.
- A variável `REPORT_JOB_TOKEN` é opcional; só habilita a rota remota de agendamento quando corresponde ao segredo GitHub Actions `CBA_REPORT_JOB_TOKEN`.
- Rotas novas: `/analytics`, `/plans`, `/sla`, `/reports`, `/forecast`, `/assistant`, `/api/session`, `/api/offline/damages` e `/damages/<id>/qr.png`.
- Modo offline: `/static/offline.html` depois de uma visita autenticada online. Não funciona com fotos, não cifra os dados salvos no dispositivo e não deve ser usado em aparelhos compartilhados.

## Testes

Após enviar para o GitHub, a aba Actions executará `pytest` via `.github/workflows/tests.yml` em cada push. Sem as dependências instaladas neste ambiente de geração, foi possível fazer apenas verificações estáticas locais: compilação Python e parsing dos templates. **Não há declaração de aprovação dos testes funcionais ou de banco PostgreSQL nesta entrega.**

## Precauções

- Não vincule WMS/ERP sem autorização da TI.
- Use dados fictícios; fotos e filas offline podem conter informações sensíveis.
- Não interprete previsões estatísticas como probabilidades garantidas.
- Antes do merge: backup restaurável, testes automáticos verdes, homologação do fluxo completo e validação das permissões por perfil/CD.
