# Dashboard Fluxo de Caixa - Newcore

Dashboard em tempo real que lê despesas do Google Sheets e recebíveis do MySQL.
**Não altera nenhuma estrutura do banco de dados.**

## Funcionalidades

- Saldo em conta atualizado
- Despesas e recebíveis do dia
- Projeção de saldo (7, 15, 30 dias)
- Gráfico de fluxo de caixa diário
- Despesas por categoria
- Próximos vencimentos
- Realizado mensal

## Setup (10 minutos)

### 1. Instalar dependências

```bash
pip install streamlit pandas gspread google-auth mysql-connector-python plotly
```

### 2. Configurar Google Sheets API

1. Acesse [Google Cloud Console](https://console.cloud.google.com/)
2. Crie um novo projeto (ou use existente)
3. Ative a **Google Sheets API** e **Google Drive API**
4. Vá em **APIs & Services > Credentials**
5. Clique em **Create Credentials > Service Account**
6. Dê um nome e clique em **Done**
7. Clique na Service Account criada > **Keys > Add Key > Create new key > JSON**
8. Salve o arquivo como `credentials.json` na mesma pasta do script

### 3. Compartilhar a planilha

1. Abra o arquivo `credentials.json`
2. Copie o valor de `"client_email"` (algo como `xxx@projeto.iam.gserviceaccount.com`)
3. Abra sua planilha no Google Sheets
4. Clique em **Compartilhar**
5. Cole o email da Service Account e dê permissão de **Leitor**

### 4. Pegar o ID da planilha

A URL da sua planilha é algo como:
```
https://docs.google.com/spreadsheets/d/1ABC123xyz.../edit
```

O ID é a parte entre `/d/` e `/edit`:
```
1ABC123xyz...
```

### 5. Configurar o script

Edite `fluxo_caixa_app.py` e preencha:

```python
# Google Sheets
GOOGLE_SHEET_ID = '1ABC123xyz...'  # ID da planilha
CREDENTIALS_PATH = 'credentials.json'

# MySQL (somente leitura)
MYSQL_CONFIG = {
    'host': 'SEU_HOST',
    'port': 3306,
    'user': 'SEU_USUARIO',
    'password': 'SUA_SENHA',
    'database': 'newcore'
}
```

### 6. Rodar

```bash
streamlit run fluxo_caixa_app.py
```

Acesse: http://localhost:8501

## Deploy (opcional)

### Streamlit Cloud (gratuito)

1. Suba o código para um repositório GitHub
2. Acesse [share.streamlit.io](https://share.streamlit.io)
3. Conecte seu GitHub e selecione o repositório
4. Configure os **Secrets** (Settings > Secrets):

```toml
[mysql]
host = "SEU_HOST"
port = 3306
user = "SEU_USUARIO"
password = "SUA_SENHA"
database = "newcore"

[gsheets]
sheet_id = "1ABC123xyz..."

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

## Estrutura esperada da planilha

### Aba DESPESAS
| Coluna | Descrição |
|--------|-----------|
| DT_VENC_ORIG | Data vencimento original |
| DT_PREV_PGTO | Data prevista pagamento |
| DT_EFET_PGTO | Data efetiva pagamento |
| FORNECEDOR | Nome do fornecedor |
| CATEGORIA CONSOLIDADA | Categoria da despesa |
| VALOR | Valor em R$ |
| STATUS Consolidado | Lançado, Previsto, Confirmado |
| ANO_ORIGINAL | Ano |
| MES_ORIGINAL | Mês |

### Aba Din DESPESAS
| Célula | Conteúdo |
|--------|----------|
| C10 | Saldo em conta |
| D10 | Data/hora atualização |
