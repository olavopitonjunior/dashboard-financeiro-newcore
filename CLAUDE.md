# CLAUDE.md - Dashboard Fluxo de Caixa Newcore

## Visão Geral

Dashboard de fluxo de caixa em tempo real para a Newcore (imobiliária digital brasileira). Lê despesas do Google Sheets e recebíveis do MySQL, exibindo projeções financeiras diárias sem alterar nenhuma estrutura de banco de dados.

## Stack

- **Frontend/Dashboard:** Streamlit
- **Visualização:** Plotly
- **Dados:** Pandas
- **Fontes:**
  - Google Sheets (despesas + saldo em conta)
  - MySQL (recebíveis - tabelas `homeoffers` + `homeofferscharges`)
- **Autenticação Google:** gspread + google-auth (Service Account)

## Estrutura do Projeto

```
fluxo-caixa-newcore/
├── fluxo_caixa_app.py      # Aplicação principal Streamlit
├── verificar_planilha.py    # Verificação E2E (Sheets + MySQL vs dashboard)
├── relatorio_email.py       # Relatório diário por email
├── requirements.txt         # Dependências
├── credentials.json         # Service Account Google (não versionar)
├── .env                     # Variáveis de ambiente (não versionar)
├── .gitignore
├── CLAUDE.md
├── Spec.md
├── backend/                 # API FastAPI (React frontend)
└── frontend/                # Frontend React + Vite
```

## Fontes de Dados

### Google Sheets

**Planilha:** FLUXO_CAIXA_NC_2025

| Aba | Conteúdo |
|-----|----------|
| DESPESAS | ~4.000 registros de despesas (2022-2026) |
| Din DESPESAS | Saldo em conta (célula C10) + data atualização (D10) |

**Colunas principais (aba DESPESAS):**
- `DT_VENC_ORIG` - Data vencimento original
- `DT_PREV_PGTO` - Data prevista pagamento (usar esta para projeções)
- `DT_EFET_PGTO` - Data efetiva pagamento
- `FORNECEDOR` - Nome do fornecedor
- `CATEGORIA CONSOLIDADA` - Categoria (FOLHA, MIDIA, SISTEMAS, etc.)
- `VALOR` - Valor em R$
- `STATUS Consolidado` - Previsto (pendente) | Lançado (pendente) | Confirmado (pago) | Write off (excluído)
- `ANO_ORIGINAL`, `MES_ORIGINAL` - Período

### MySQL (somente leitura)

**Database:** newcore

**Tabela:** `homeoffers` (ofertas)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| Id | INT | PK |
| Audit | TINYINT | 0 = oferta válida, 1 = auditoria |
| PublishStatus_Id | INT | 30 = publicada |

**Tabela:** `homeofferscharges` (recebíveis/cobranças)

| Campo | Tipo | Descrição |
|-------|------|-----------|
| Id | INT | PK |
| Offer_Id | INT | FK para homeoffers |
| ExpiresAt | DATETIME | Data vencimento |
| PaidAt | DATETIME | Data pagamento (NULL = pendente) |
| Value | DECIMAL | Valor da cobrança |
| ChargeStatus | VARCHAR | Status |

**Query padrão recebíveis:**
```sql
SELECT
    DATE(b.ExpiresAt) as data_vencimento,
    b.Value as valor,
    b.PaidAt as data_pagamento,
    b.ChargeStatus as status,
    b.Offer_Id as oferta_id
FROM homeoffers a
INNER JOIN homeofferscharges b ON a.Id = b.Offer_Id
WHERE a.Audit = 0
  AND a.PublishStatus_Id = 30
  AND b.PaidAt IS NULL
ORDER BY b.ExpiresAt
```

## Regras de Negócio

1. **Despesas pendentes:** `STATUS Consolidado` IN ('Previsto', 'Lançado')
2. **Despesas vencidas:** `STATUS Consolidado` = 'Lançado' AND `DT_PREV_PGTO` < hoje
3. **Despesas pagas:** `STATUS Consolidado` = 'Confirmado'
4. **Write off:** Excluído de todos os cálculos
5. **Orçado:** STATUS IN ('Previsto', 'Lançado') | **Realizado:** STATUS = 'Confirmado'
6. **Recebíveis pendentes:** `PaidAt IS NULL` + `Audit = 0` + `PublishStatus_Id = 30`
7. **Saldo projetado:** `Saldo Conta + Recebíveis - Despesas`
8. **Períodos de análise:** Hoje, 7 dias, 15 dias, 30 dias

## Configuração

### Variáveis necessárias

```python
# Google Sheets
GOOGLE_SHEET_ID = 'ID_DA_PLANILHA'
CREDENTIALS_PATH = 'credentials.json'

# MySQL
MYSQL_CONFIG = {
    'host': 'HOST',
    'port': 3306,
    'user': 'USUARIO',
    'password': 'SENHA',
    'database': 'newcore'
}
```

### Service Account Google

1. Criar projeto no Google Cloud Console
2. Ativar Google Sheets API e Google Drive API
3. Criar Service Account e baixar JSON
4. Compartilhar planilha com email da Service Account (Leitor)

## Comandos

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar localmente
streamlit run fluxo_caixa_app.py

# Limpar cache Streamlit
streamlit cache clear
```

## Restrições Importantes

1. **NÃO criar tabelas/views no MySQL** - banco é somente leitura
2. **NÃO modificar a planilha** - apenas leitura
3. **Cache de 5 minutos** - evitar excesso de requisições
4. **Dados sensíveis** - não versionar credentials.json nem .env

## Padrões de Código

- Funções de leitura com `@st.cache_data(ttl=300)`
- Tratamento de erros com try/except e mensagens amigáveis
- Datas sempre em `pd.Timestamp` normalizado
- Valores monetários com `DECIMAL(18,2)`
- Formatação BR: `R$ {:,.2f}` e datas `%d/%m/%Y`

## Próximas Evoluções (backlog)

- [x] Relatório diário automatizado (email)
- [x] Orçado vs Realizado por categoria
- [x] Alertas de vencimento
- [x] Deploy no Streamlit Cloud
- [ ] Autenticação de usuários
