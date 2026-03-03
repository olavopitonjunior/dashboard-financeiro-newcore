# Spec.md - Dashboard Fluxo de Caixa Newcore

## 1. Objetivo

Criar um dashboard de fluxo de caixa em tempo real que consolide despesas (Google Sheets) e recebíveis (MySQL) para projeção financeira diária da Newcore.

## 2. Usuários

- **Primário:** Equipe Financeiro (alimenta saldo diariamente)
- **Secundário:** Gestão/Diretoria (consulta projeções)

## 3. Requisitos Funcionais

### 3.1 Visão Geral (KPIs)

| KPI | Fonte | Cálculo |
|-----|-------|---------|
| Saldo em Conta | Sheets (Din DESPESAS, C10) | Valor direto |
| Despesas Hoje | Sheets (DESPESAS) | SUM(VALOR) WHERE DT_PREV_PGTO = hoje AND STATUS IN (Previsto, Confirmado) |
| Recebíveis Hoje | MySQL (homeofferscharges) | SUM(Value) WHERE DATE(ExpiresAt) = hoje AND PaidAt IS NULL |
| Saldo Projetado | Calculado | Saldo + Recebíveis - Despesas |

### 3.2 Projeção por Período

Mostrar para 7, 15 e 30 dias:
- Total de despesas previstas
- Total de recebíveis esperados
- Saldo do período (Recebíveis - Despesas)

### 3.3 Gráfico Fluxo Diário

- **Tipo:** Barras (despesas negativas, recebíveis positivos) + linha (saldo acumulado)
- **Período:** Próximos 30 dias
- **Eixo Y1:** Movimentação diária (R$)
- **Eixo Y2:** Saldo acumulado (R$)

### 3.4 Despesas por Categoria

- **Tipo:** Pizza/Donut
- **Filtro:** Próximos 30 dias, STATUS IN (Previsto, Confirmado)
- **Agrupamento:** CATEGORIA CONSOLIDADA

### 3.5 Próximos Vencimentos

- **Tipo:** Tabela
- **Filtro:** Próximos 7 dias, STATUS IN (Previsto, Confirmado)
- **Colunas:** Data | Fornecedor | Categoria | Valor
- **Ordenação:** Data ASC

### 3.6 Realizado Mensal

- **Tipo:** Barras verticais
- **Filtro:** ANO_ORIGINAL = 2025, STATUS = Lançado
- **Agrupamento:** MES_ORIGINAL
- **Valores:** SUM(VALOR)

## 4. Requisitos Não-Funcionais

| Requisito | Especificação |
|-----------|---------------|
| Performance | Carregamento < 5s |
| Cache | 5 minutos (TTL) |
| Disponibilidade | Local ou Streamlit Cloud |
| Segurança | Credenciais em arquivo separado, não versionado |
| Banco de dados | Somente SELECT, sem DDL/DML |

## 5. Arquitetura

```
┌─────────────────┐     ┌─────────────────┐
│  Google Sheets  │     │     MySQL       │
│                 │     │    (newcore)    │
│  - DESPESAS     │     │                 │
│  - Din DESPESAS │     │ homeofferscharges│
└────────┬────────┘     └────────┬────────┘
         │                       │
         │  gspread              │  mysql-connector
         │                       │
         └───────────┬───────────┘
                     │
              ┌──────▼──────┐
              │   Pandas    │
              │ (DataFrames)│
              └──────┬──────┘
                     │
              ┌──────▼──────┐
              │  Streamlit  │
              │  + Plotly   │
              └──────┬──────┘
                     │
              ┌──────▼──────┐
              │   Browser   │
              │ (Dashboard) │
              └─────────────┘
```

## 6. Modelo de Dados

### 6.1 DataFrame Despesas (df_despesas)

```python
{
    'DT_VENC_ORIG': datetime64,
    'DT_PREV_PGTO': datetime64,
    'DT_EFET_PGTO': datetime64,
    'FORNECEDOR': str,
    'CNPJ OMIE': str,
    'NOME FORNECEDOR OMIE': str,
    'POSSUI CONTRATO?': str,
    'CATEGORIA CONSOLIDADA': str,
    'DEPTO': str,
    'VALOR': float64,
    'RECORRÊNCIA': str,
    'STATUS Consolidado': str,  # Lançado | Previsto | Confirmado | Write off
    'OBSERVAÇÕES': str,
    'LISTA SRK': str,
    'RESPONSÁVEL': str,
    'DEPTO Detalhado': str,
    'CATEGORIA': str,
    'Ano consolidado': int,
    'ANO_ORIGINAL': int,
    'MES_ORIGINAL': int
}
```

### 6.2 DataFrame Recebíveis (df_recebiveis)

```python
{
    'data_vencimento': datetime64,
    'valor': float64,
    'data_pagamento': datetime64,  # NaT = pendente
    'status': str
}
```

### 6.3 DataFrame Fluxo Diário (df_fluxo)

```python
{
    'Data': datetime64,
    'Despesas': float64,
    'Recebíveis': float64,
    'Saldo Dia': float64,  # Recebíveis - Despesas
    'Saldo Acumulado': float64  # Saldo Conta + cumsum(Saldo Dia)
}
```

## 7. Funções Principais

### 7.1 Leitura

```python
@st.cache_data(ttl=300)
def load_despesas_from_gsheets() -> pd.DataFrame

@st.cache_data(ttl=300)
def load_saldo_from_gsheets() -> tuple[float, str]

@st.cache_data(ttl=300)
def load_recebiveis_from_mysql() -> pd.DataFrame
```

### 7.2 Análise

```python
def calcular_despesas_periodo(df: pd.DataFrame, dias: int) -> float

def calcular_recebiveis_periodo(df: pd.DataFrame, dias: int) -> float

def gerar_fluxo_diario(df_despesas: pd.DataFrame, df_recebiveis: pd.DataFrame, dias: int = 30) -> pd.DataFrame
```

### 7.3 Interface

```python
def main():
    # Layout Streamlit
    # Linha 1: KPIs (4 colunas)
    # Linha 2: Projeção por período (3 colunas)
    # Linha 3: Gráfico fluxo diário (full width)
    # Linha 4: Pizza categorias + Tabela vencimentos (2 colunas)
    # Linha 5: Barras realizado mensal (full width)
```

## 8. Configuração

### 8.1 Arquivo .env (não versionar)

```env
GOOGLE_SHEET_ID=1ABC123xyz...
GOOGLE_CREDENTIALS_PATH=credentials.json

MYSQL_HOST=host.exemplo.com
MYSQL_PORT=3306
MYSQL_USER=usuario
MYSQL_PASSWORD=senha
MYSQL_DATABASE=newcore
```

### 8.2 Arquivo .gitignore

```gitignore
credentials.json
.env
__pycache__/
*.pyc
.streamlit/secrets.toml
```

## 9. Deploy

### 9.1 Local

```bash
pip install -r requirements.txt
streamlit run fluxo_caixa_app.py
```

### 9.2 Streamlit Cloud

1. Repositório GitHub (público ou privado)
2. Conectar em share.streamlit.io
3. Configurar Secrets (Settings > Secrets)

```toml
[mysql]
host = "HOST"
port = 3306
user = "USUARIO"
password = "SENHA"
database = "newcore"

[gsheets]
sheet_id = "ID_PLANILHA"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
```

## 10. Testes

### 10.1 Casos de Teste

| Cenário | Entrada | Resultado Esperado |
|---------|---------|-------------------|
| Sem despesas hoje | DT_PREV_PGTO != hoje | Despesas Hoje = R$ 0,00 |
| Sem recebíveis hoje | ExpiresAt != hoje | Recebíveis Hoje = R$ 0,00 |
| Saldo negativo | Despesas > Recebíveis | Cor vermelha no KPI |
| Planilha vazia | Aba sem dados | Mensagem de warning |
| MySQL offline | Conexão falha | Mensagem de erro |
| Cache expirado | TTL > 300s | Recarrega dados |

### 10.2 Validações

- Datas devem ser convertidas para datetime64
- Valores devem ser numéricos (float)
- STATUS deve estar na lista válida
- Saldo em conta deve existir na célula C10

## 11. Cronograma

| Fase | Atividade | Tempo |
|------|-----------|-------|
| 1 | Configurar Google Cloud + Service Account | 15 min |
| 2 | Configurar credenciais MySQL | 5 min |
| 3 | Testar conexões isoladamente | 10 min |
| 4 | Rodar dashboard local | 5 min |
| 5 | Ajustes de layout/filtros | 30 min |
| 6 | Deploy Streamlit Cloud (opcional) | 15 min |

**Total estimado:** 1h20 (sem deploy) / 1h35 (com deploy)

## 12. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|---------------|---------|-----------|
| Planilha muda estrutura | Média | Alto | Validar colunas no load |
| MySQL indisponível | Baixa | Alto | Try/except + fallback |
| Rate limit Google API | Baixa | Médio | Cache de 5 min |
| Dados inconsistentes | Média | Médio | Filtros de validação |
