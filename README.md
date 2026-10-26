# CBA 2.0 — Controle de Avarias

**Versão piloto** para testar registros e aprovações em um centro de distribuição, via computador e celular. **Não homologado para informações corporativas reais**.

## Acesso

- Link existente no Render: https://cba-controle-avarias.onrender.com
- Usuário inicial: `admin`
- Senha: definida pelo responsável no Render, variável `ADMIN_PASSWORD`. A aplicação cria o `admin` apenas se não existir. **Alterar a variável não redefine automaticamente a senha de um usuário existente.**
- Banco existente: PostgreSQL `cba-db` no Render. Conta gratuita com expiração agendada em **14/11/2026**. **Fazer backup e migrar a um plano persistente antes do vencimento**.

## Funcionalidades implementadas nesta base 2.0

1. Cadastro de avarias com SKU, produto, quantidade, valor, etapa, turno e localização.
2. Fotos opcionais (até três, JPG/PNG/WebP), redimensionadas e armazenadas em tabela protegida por login.
3. Identificação de CD, setor e responsável informado.
4. Lote e validade opcionais.
5. Classificação por tipo, causa e gravidade.
6. Número de protocolo e detalhes completos.
7. Fluxo `Aberta → Em análise → Aguardando aprovação → Aprovada → Concluída`, com rejeição e devolução à análise.
8. Tratativa/destinação e valor recuperado. Conclusão exige destinação.
9. Auditoria para criação, tratamento e movimentação de status.
10. Papéis de acesso: operador, líder, gestor e administrador; isolamento básico por unidade.
11. Indicadores de volume, ocorrências em tratamento, atrasos acima de 48 horas e perdas líquidas.
12. Distribuição por etapa, principais causas.
13. Filtros por SKU, status, etapa, causa, data e CD (admin).
14. Exportação CSV compatível com Excel (com mitigação para fórmulas em CSV).
15. Proteção CSRF, cookies HTTPOnly/Secure em produção, cabeçalhos de segurança e limite de tentativas de login.
16. Layout responsivo para celular.
17. Migração aditiva compatível com tabelas `user`/`damage` do CBA 1.x.

## Implantar atualização na instalação existente

**ATENÇÃO:** faça backup do banco antes. O CBA 2.0 modifica o esquema existente adicionando colunas. Use ambiente de homologação primeiro.

1. Copie o conteúdo do ZIP para a **raiz** do repositório `mauricioviana1-maker/cba-controle-avarias`, mantendo `templates` e `static` com seus arquivos. **Não publique `.env`**.
2. A alteração na `main` aciona o Render por auto-deploy. Confira em Dashboard → Deploys se o commit está em `Live`.
3. Mantenha no serviço Render as variáveis já configuradas `DATABASE_URL`, `SECRET_KEY`, `ADMIN_PASSWORD`, `FLASK_ENV=production`.
4. Verifique `https://cba-controle-avarias.onrender.com/health`: deve retornar `{"status":"ok","version":"2.0.0"}`.
5. Abra `/login`, entre como administrador, crie um usuário operador e teste o fluxo com dados **fictícios**.
6. Caso o `admin` antigo tenha perfil `gestor`, ele será promovido para `admin`, preservando sua senha e seus registros.

## Migração segura

No boot o sistema detecta colunas ausentes nas tabelas `user` e `damage` e adiciona **apenas as necessárias**; as tabelas novas são `site`, `damage_photo`, `audit_log`, `login_failure`. O fluxo não exclui colunas nem dados da versão anterior. Registros antigos continuam consultáveis, mas campos inexistentes aparecem vazios.

Para implantação empresarial, substituir a migração automática por Alembic com versões de schema e plano de rollback, em ambiente de teste.

## Desenvolvimento local

Exemplo usando PostgreSQL já configurado e variáveis exportadas no terminal:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export FLASK_ENV=development
export SECRET_KEY='chave-aleatoria-com-no-minimo-32-caracteres'
export DATABASE_URL='postgresql://usuario:senha@localhost:5432/cba'
export ADMIN_PASSWORD='senha-inicial-com-pelo-menos-12-caracteres'
python app.py
```

## Limites e segurança antes de implantação oficial

- Banco gratuito tem validade limitada; backups/restore, retenção e monitoramento são obrigatórios em produção.
- Fotos estão armazenadas no PostgreSQL (piloto); mover para armazenamento privado (p.ex., Supabase Storage/S3) antes do uso intensivo.
- Sem SSO/MFA, permissões corporativas completas, redefinição segura de senha, scanner de códigos, integração WMS/ERP ou operação offline.
- Dashboard resume no máximo 500 registros acessíveis: não deve ser interpretado como relatório histórico exaustivo.
- Taxa de login é limitada por IP+usuário e pode exigir WAF no ambiente corporativo.
- `ADMIN_PASSWORD` apenas inicializa uma conta inexistente; não substitui senhas já armazenadas.
- Não compartilhar senhas nem URLs de conexão.

## Validação

`python -m pytest -q` executa testes de sintaxe comportamental via Flask/SQLite temporário (com o pacote instalado). Testar também em PostgreSQL de homologação e smartphone.
