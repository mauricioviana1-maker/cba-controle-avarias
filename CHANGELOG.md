# Relatório de alterações — CBA 2.0.0

## Novidades
1. Nova identidade visual CBA (sem marcas corporativas), responsiva.
2. Cadastro com CD, setor, operador informado, lote, vencimento, tipo, causa e criticidade.
3. Fotos até 3 por lançamento, com compactação JPG e autenticação na leitura.
4. Histórico auditável de criação, status e tratamento.
5. Estados de fluxo com análise, aprovação, rejeição e conclusão.
6. Controle de destino e de valor recuperado; bloqueio de encerramento sem destinação.
7. Perfis operador, líder, gestor e administrador; CD vinculado ao acesso.
8. Criação de usuários e centros de distribuição para administrador.
9. Visão gerencial com indicadores de ocorrências, atrasos e perdas, etapas e causas.
10. Busca filtrada por SKU, estado, causa, etapa, período e unidade (admin).
11. Exportação CSV/Excel com identificação por protocolo e redução de risco de injeção de fórmulas.
12. Fotos guardadas fora de diretório público; limites de quantidade, dimensão e upload.
13. Cabeçalhos de proteção, CSRF e bloqueio temporário após 5 tentativas falhas em 15 minutos por usuário/IP.
14. Migração aditiva da base da versão 1.x, sem excluir registros antigos.
15. Endpoint de saúde `/health` com verificação de banco e versão.

## Não incluído na versão 2.0
WMS/ERP, scanner, offline/PWA, envio de notificações externas, workflow de ações preventivas, migração para Supabase, SSO/MFA, armazenamento em nuvem de arquivos separado do banco, backup contínuo, relatórios XLSX/PDF e histórico de alterações de campos livres.

## Necessário antes de usar na empresa
Revisão de acesso por perfil, homologação da TI, migração para banco persistente, backup/restore testados, proteção de imagens e registros conforme políticas internas e LGPD, avaliação de segurança e performance.
