"""
Dashboard Fluxo de Caixa - Newcore
==================================
Lê despesas do Google Sheets e recebíveis do MySQL.
Não altera nenhuma estrutura de banco de dados.

Requisitos:
    pip install streamlit pandas gspread google-auth mysql-connector-python plotly python-dotenv

Uso:
    streamlit run fluxo_caixa_app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import mysql.connector
import gspread
from google.oauth2.service_account import Credentials
import os
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env
load_dotenv()

# ============================================
# CONFIGURAÇÕES (via .env ou Streamlit Secrets)
# ============================================

# Google Sheets
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID') or st.secrets.get('gsheets', {}).get('sheet_id')
CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

# MySQL (somente leitura)
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST') or st.secrets.get('mysql', {}).get('host'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'user': os.getenv('MYSQL_USER') or st.secrets.get('mysql', {}).get('user'),
    'password': os.getenv('MYSQL_PASSWORD') or st.secrets.get('mysql', {}).get('password'),
    'database': os.getenv('MYSQL_DATABASE', 'newcore')
}

# ============================================
# FUNÇÕES DE LEITURA
# ============================================

def _get_gsheets_client():
    """Retorna cliente gspread autenticado (reutilizável)"""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    # Streamlit Cloud: credentials via st.secrets
    if os.path.exists(CREDENTIALS_PATH):
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    else:
        creds = Credentials.from_service_account_info(
            dict(st.secrets['gcp_service_account']), scopes=scopes
        )
    return gspread.authorize(creds)


@st.cache_data(ttl=300)  # Cache de 5 minutos
def load_despesas_from_gsheets():
    """Lê aba DESPESAS do Google Sheets"""
    try:
        client = _get_gsheets_client()

        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sheet.worksheet('DESPESAS')
        
        all_values = worksheet.get_all_values()
        headers = all_values[0]
        # Filtra colunas com header vazio (planilha tem colunas extras)
        valid_cols = [i for i, h in enumerate(headers) if h.strip()]
        filtered_headers = [headers[i] for i in valid_cols]
        rows = [[row[i] if i < len(row) else '' for i in valid_cols] for row in all_values[1:]]
        df = pd.DataFrame(rows, columns=filtered_headers)
        
        # Converte datas (formato DD/MM/YYYY da planilha)
        for col in ['DT_VENC_ORIG', 'DT_PREV_PGTO', 'DT_EFET_PGTO']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

        # Converte valor (formato BR: "R$ 1.234,56")
        if 'VALOR' in df.columns:
            df['VALOR'] = (df['VALOR'].astype(str)
                           .str.replace('R$', '', regex=False)
                           .str.replace('.', '', regex=False)
                           .str.replace(',', '.', regex=False)
                           .str.strip())
            df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce')

        # Normaliza STATUS (Lançado e LANÇADO → Lançado)
        if 'STATUS Consolidado' in df.columns:
            df['STATUS Consolidado'] = df['STATUS Consolidado'].str.strip().str.capitalize()

        # Converte ANO/MES para numérico
        for col in ['ANO_ORIGINAL', 'MES_ORIGINAL']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Valida colunas obrigatórias
        required = ['DT_PREV_PGTO', 'VALOR', 'STATUS Consolidado',
                     'CATEGORIA CONSOLIDADA', 'FORNECEDOR', 'ANO_ORIGINAL', 'MES_ORIGINAL']
        missing = [c for c in required if c not in df.columns]
        if missing:
            st.error(f"Colunas ausentes na planilha: {', '.join(missing)}")
            return pd.DataFrame()

        return df
    
    except Exception as e:
        st.error(f"Erro ao ler Google Sheets: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_saldo_from_gsheets():
    """Lê saldo em conta da aba Din DESPESAS"""
    try:
        client = _get_gsheets_client()

        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sheet.worksheet('Din DESPESAS')
        
        # Saldo está na célula C10 (linha 10, coluna 3)
        saldo = worksheet.cell(10, 3).value
        saldo_str = str(saldo).replace('R$', '').strip()
        saldo_str = saldo_str.replace('.', '').replace(',', '.')
        saldo = float(saldo_str)
        
        # Data de atualização está em D10
        data_str = worksheet.cell(10, 4).value
        
        return saldo, data_str
    
    except Exception as e:
        st.error(f"Erro ao ler saldo: {e}")
        return 0, "N/A"


@st.cache_data(ttl=300)
def load_recebiveis_from_mysql():
    """Lê recebíveis do MySQL (somente SELECT)"""
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        try:
            query = """
                SELECT
                    DATE(ExpiresAt) as data_vencimento,
                    Value as valor,
                    PaidAt as data_pagamento,
                    ChargeStatus as status,
                    Offer_Id as oferta_id
                FROM homeofferscharges
                WHERE ExpiresAt >= CURDATE() - INTERVAL 90 DAY
                ORDER BY ExpiresAt
            """

            df = pd.read_sql(query, conn)
        finally:
            conn.close()

        df['data_vencimento'] = pd.to_datetime(df['data_vencimento'])
        df['valor'] = pd.to_numeric(df['valor'], errors='coerce')

        return df

    except Exception as e:
        st.error(f"Erro ao ler MySQL: {e}")
        return pd.DataFrame()


# ============================================
# FUNÇÕES DE ANÁLISE
# ============================================

def calcular_despesas_periodo(df, dias):
    """Calcula total de despesas para os próximos X dias"""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    
    mask = (
        (df['DT_PREV_PGTO'] >= hoje) & 
        (df['DT_PREV_PGTO'] <= fim) &
        (df['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    )
    
    return df[mask]['VALOR'].sum()


def calcular_recebiveis_periodo(df, dias):
    """Calcula total de recebíveis para os próximos X dias"""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    
    mask = (
        (df['data_vencimento'] >= hoje) & 
        (df['data_vencimento'] <= fim) &
        (df['data_pagamento'].isna())  # Ainda não pago
    )
    
    return df[mask]['valor'].sum()


def gerar_fluxo_diario(df_despesas, df_recebiveis, dias=30):
    """Gera fluxo de caixa diário consolidado"""
    hoje = pd.Timestamp.now().normalize()
    datas = pd.date_range(hoje, periods=dias, freq='D')
    
    fluxo = []
    
    for data in datas:
        # Despesas do dia
        desp = df_despesas[
            (df_despesas['DT_PREV_PGTO'] == data) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ]['VALOR'].sum()
        
        # Recebíveis do dia
        receb = df_recebiveis[
            (df_recebiveis['data_vencimento'] == data) &
            (df_recebiveis['data_pagamento'].isna())
        ]['valor'].sum()
        
        fluxo.append({
            'Data': data,
            'Despesas': desp,
            'Recebíveis': receb,
            'Saldo Dia': receb - desp
        })
    
    return pd.DataFrame(fluxo)


# ============================================
# INTERFACE STREAMLIT
# ============================================

def main():
    st.set_page_config(
        page_title="Fluxo de Caixa - Newcore",
        page_icon="💰",
        layout="wide"
    )
    
    st.title("💰 Fluxo de Caixa")
    st.caption(f"Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    
    # Botão de refresh
    if st.button("🔄 Atualizar dados"):
        st.cache_data.clear()
        st.rerun()
    
    # Carrega dados
    with st.spinner("Carregando dados..."):
        df_despesas = load_despesas_from_gsheets()
        saldo_conta, data_saldo = load_saldo_from_gsheets()
        df_recebiveis = load_recebiveis_from_mysql()
    
    hoje = pd.Timestamp.now().normalize()

    if df_despesas.empty:
        st.warning("Não foi possível carregar as despesas. Verifique as configurações.")
        return
    
    # ==========================================
    # LINHA 1: KPIs PRINCIPAIS
    # ==========================================
    
    st.markdown("---")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="💵 Saldo em Conta",
            value=f"R$ {saldo_conta:,.2f}",
            help=f"Atualizado em: {data_saldo}"
        )
    
    despesas_hoje = calcular_despesas_periodo(df_despesas, 0)
    with col2:
        st.metric(
            label="📤 Despesas Hoje",
            value=f"R$ {despesas_hoje:,.2f}"
        )
    
    recebiveis_hoje = calcular_recebiveis_periodo(df_recebiveis, 0)
    with col3:
        st.metric(
            label="📥 Recebíveis Hoje",
            value=f"R$ {recebiveis_hoje:,.2f}"
        )
    
    saldo_projetado = saldo_conta - despesas_hoje + recebiveis_hoje
    with col4:
        delta_color = "normal" if saldo_projetado >= 0 else "inverse"
        st.metric(
            label="📊 Saldo Projetado (fim do dia)",
            value=f"R$ {saldo_projetado:,.2f}",
            delta=f"R$ {(recebiveis_hoje - despesas_hoje):,.2f}",
            delta_color=delta_color
        )
    
    # ==========================================
    # ALERTAS DE VENCIMENTO
    # ==========================================

    # Despesas vencidas (data passou e ainda não foi paga/lançada)
    df_desp_vencidas = df_despesas[
        (df_despesas['DT_PREV_PGTO'] < hoje) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ].copy()

    # Recebíveis vencidos (data passou e não foi pago)
    df_rec_vencidos = df_recebiveis[
        (df_recebiveis['data_vencimento'] < hoje) &
        (df_recebiveis['data_pagamento'].isna())
    ].copy()

    total_alertas = len(df_desp_vencidas) + len(df_rec_vencidos)

    if total_alertas > 0:
        valor_desp_vencidas = df_desp_vencidas['VALOR'].sum()
        valor_rec_vencidos = df_rec_vencidos['valor'].sum()

        msg = f"**{total_alertas} itens vencidos** — "
        partes = []
        if len(df_desp_vencidas) > 0:
            partes.append(f"{len(df_desp_vencidas)} despesas (R$ {valor_desp_vencidas:,.2f})")
        if len(df_rec_vencidos) > 0:
            partes.append(f"{len(df_rec_vencidos)} recebíveis (R$ {valor_rec_vencidos:,.2f})")
        msg += " | ".join(partes)

        if total_alertas >= 3:
            st.error(msg)
        else:
            st.warning(msg)

        with st.expander("Ver itens vencidos"):
            col_v1, col_v2 = st.columns(2)

            with col_v1:
                st.markdown("**Despesas vencidas**")
                if not df_desp_vencidas.empty:
                    df_desp_vencidas['Dias em atraso'] = (hoje - df_desp_vencidas['DT_PREV_PGTO']).dt.days
                    df_show = df_desp_vencidas[['DT_PREV_PGTO', 'FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR', 'Dias em atraso']].sort_values('Dias em atraso', ascending=False)
                    df_show.columns = ['Data', 'Fornecedor', 'Categoria', 'Valor', 'Dias atraso']
                    st.dataframe(
                        df_show,
                        column_config={
                            'Data': st.column_config.DateColumn('Data', format='DD/MM/YYYY'),
                            'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Nenhuma despesa vencida.")

            with col_v2:
                st.markdown("**Recebíveis vencidos**")
                if not df_rec_vencidos.empty:
                    df_rec_vencidos['Dias em atraso'] = (hoje - df_rec_vencidos['data_vencimento']).dt.days
                    df_show_r = df_rec_vencidos[['data_vencimento', 'oferta_id', 'valor', 'status', 'Dias em atraso']].sort_values('Dias em atraso', ascending=False)
                    df_show_r.columns = ['Data', 'Oferta', 'Valor', 'Status', 'Dias atraso']
                    st.dataframe(
                        df_show_r,
                        column_config={
                            'Data': st.column_config.DateColumn('Data', format='DD/MM/YYYY'),
                            'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Nenhum recebível vencido.")

    # ==========================================
    # LINHA 2: PROJEÇÃO DE PERÍODOS
    # ==========================================

    st.markdown("---")
    st.subheader("📅 Projeção por Período")
    
    col1, col2, col3 = st.columns(3)
    
    periodos = [7, 15, 30]
    
    for col, dias in zip([col1, col2, col3], periodos):
        with col:
            desp = calcular_despesas_periodo(df_despesas, dias)
            receb = calcular_recebiveis_periodo(df_recebiveis, dias)
            saldo = receb - desp
            
            st.markdown(f"**Próximos {dias} dias**")
            st.write(f"Despesas: R$ {desp:,.2f}")
            st.write(f"Recebíveis: R$ {receb:,.2f}")
            
            cor = "green" if saldo >= 0 else "red"
            st.markdown(f"Saldo: <span style='color:{cor}'>**R$ {saldo:,.2f}**</span>", 
                       unsafe_allow_html=True)
    
    # ==========================================
    # LINHA 3: GRÁFICO DE FLUXO DIÁRIO
    # ==========================================
    
    st.markdown("---")
    st.subheader("📈 Fluxo de Caixa Diário (30 dias)")
    
    df_fluxo = gerar_fluxo_diario(df_despesas, df_recebiveis, 30)
    
    # Calcula saldo acumulado
    df_fluxo['Saldo Acumulado'] = saldo_conta + df_fluxo['Saldo Dia'].cumsum()
    
    fig = go.Figure()
    
    fig.add_trace(go.Bar(
        x=df_fluxo['Data'],
        y=df_fluxo['Recebíveis'],
        name='Recebíveis',
        marker_color='#2ecc71'
    ))
    
    fig.add_trace(go.Bar(
        x=df_fluxo['Data'],
        y=-df_fluxo['Despesas'],
        name='Despesas',
        marker_color='#e74c3c'
    ))
    
    fig.add_trace(go.Scatter(
        x=df_fluxo['Data'],
        y=df_fluxo['Saldo Acumulado'],
        name='Saldo Acumulado',
        line=dict(color='#3498db', width=3),
        yaxis='y2'
    ))
    
    fig.update_layout(
        barmode='relative',
        yaxis=dict(title='Movimentação (R$)'),
        yaxis2=dict(title='Saldo Acumulado (R$)', overlaying='y', side='right'),
        legend=dict(orientation='h', yanchor='bottom', y=1.02),
        height=400
    )
    
    st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # LINHA 3.5: DETALHAMENTO DIÁRIO
    # ==========================================

    st.markdown("---")
    st.subheader("🔍 Detalhamento Diário")

    # Tabela consolidada (próximos 30 dias)
    df_resumo = df_fluxo[['Data', 'Despesas', 'Recebíveis', 'Saldo Dia']].copy()

    st.dataframe(
        df_resumo,
        column_config={
            'Data': st.column_config.DateColumn('Data', format='DD/MM/YYYY'),
            'Despesas': st.column_config.NumberColumn('Despesas', format='R$ %.2f'),
            'Recebíveis': st.column_config.NumberColumn('Recebíveis', format='R$ %.2f'),
            'Saldo Dia': st.column_config.NumberColumn('Saldo Dia', format='R$ %.2f'),
        },
        use_container_width=True,
        hide_index=True,
        height=350
    )

    # Seletor de data
    data_selecionada = st.date_input(
        "Selecione o dia para ver detalhes",
        value=hoje.date(),
        min_value=hoje.date(),
        max_value=(hoje + timedelta(days=29)).date()
    )
    data_sel = pd.Timestamp(data_selecionada).normalize()

    # Resumo do dia selecionado
    desp_dia_total = df_despesas[
        (df_despesas['DT_PREV_PGTO'] == data_sel) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ]['VALOR'].sum()

    receb_dia_total = df_recebiveis[
        (df_recebiveis['data_vencimento'] == data_sel) &
        (df_recebiveis['data_pagamento'].isna())
    ]['valor'].sum()

    col_res1, col_res2, col_res3 = st.columns(3)
    with col_res1:
        st.metric("📤 Despesas", f"R$ {desp_dia_total:,.2f}")
    with col_res2:
        st.metric("📥 Recebíveis", f"R$ {receb_dia_total:,.2f}")
    with col_res3:
        saldo_dia_sel = receb_dia_total - desp_dia_total
        st.metric("💰 Saldo", f"R$ {saldo_dia_sel:,.2f}")

    # Tabelas de detalhe lado a lado
    col_desp, col_rec = st.columns(2)

    with col_desp:
        st.markdown("**📤 Despesas do dia**")
        df_desp_dia = df_despesas[
            (df_despesas['DT_PREV_PGTO'] == data_sel) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ][['FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR', 'STATUS Consolidado']].sort_values('VALOR', ascending=False)

        if not df_desp_dia.empty:
            df_desp_dia.columns = ['Fornecedor', 'Categoria', 'Valor', 'Status']
            st.dataframe(
                df_desp_dia,
                column_config={
                    'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhuma despesa prevista para esta data.")

    with col_rec:
        st.markdown("**📥 Recebíveis do dia**")
        df_rec_dia = df_recebiveis[
            (df_recebiveis['data_vencimento'] == data_sel) &
            (df_recebiveis['data_pagamento'].isna())
        ][['oferta_id', 'valor', 'status']].sort_values('valor', ascending=False)

        if not df_rec_dia.empty:
            df_rec_dia.columns = ['Oferta', 'Valor', 'Status']
            st.dataframe(
                df_rec_dia,
                column_config={
                    'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhum recebível pendente para esta data.")

    # ==========================================
    # LINHA 4: DESPESAS POR CATEGORIA
    # ==========================================
    
    st.markdown("---")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("🏷️ Despesas Previstas por Categoria")

        fim_mes = hoje + timedelta(days=30)
        
        df_cat = df_despesas[
            (df_despesas['DT_PREV_PGTO'] >= hoje) &
            (df_despesas['DT_PREV_PGTO'] <= fim_mes) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ].groupby('CATEGORIA CONSOLIDADA')['VALOR'].sum().sort_values(ascending=False)
        
        if not df_cat.empty:
            fig_cat = px.pie(
                values=df_cat.values,
                names=df_cat.index,
                hole=0.4
            )
            fig_cat.update_layout(height=350)
            st.plotly_chart(fig_cat, use_container_width=True)
        else:
            st.info("Nenhuma despesa prevista para os próximos 30 dias.")
    
    with col2:
        st.subheader("📋 Próximos Vencimentos")
        
        df_proximos = df_despesas[
            (df_despesas['DT_PREV_PGTO'] >= hoje) &
            (df_despesas['DT_PREV_PGTO'] <= hoje + timedelta(days=7)) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ][['DT_PREV_PGTO', 'FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR']].sort_values('DT_PREV_PGTO')
        
        if not df_proximos.empty:
            df_proximos['DT_PREV_PGTO'] = df_proximos['DT_PREV_PGTO'].dt.strftime('%d/%m')
            df_proximos['VALOR'] = df_proximos['VALOR'].apply(lambda x: f"R$ {x:,.2f}")
            df_proximos.columns = ['Data', 'Fornecedor', 'Categoria', 'Valor']
            st.dataframe(df_proximos, use_container_width=True, hide_index=True)
        else:
            st.info("Nenhum vencimento nos próximos 7 dias.")
    
    # ==========================================
    # LINHA 5: ORÇADO VS REALIZADO
    # ==========================================

    st.markdown("---")

    meses_nome = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
                  7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}

    col_filtro1, col_filtro2 = st.columns([1, 3])
    with col_filtro1:
        anos_disponiveis = sorted(df_despesas['ANO_ORIGINAL'].dropna().unique(), reverse=True)
        ano_selecionado = st.selectbox("Ano", anos_disponiveis, index=0) if anos_disponiveis else datetime.now().year
    with col_filtro2:
        categorias_disponiveis = sorted(df_despesas['CATEGORIA CONSOLIDADA'].dropna().unique())
        categorias_selecionadas = st.multiselect("Filtrar categorias", categorias_disponiveis, default=[])

    st.subheader(f"📊 Orçado vs Realizado ({ano_selecionado})")

    df_ano = df_despesas[df_despesas['ANO_ORIGINAL'] == ano_selecionado].copy()
    if categorias_selecionadas:
        df_ano = df_ano[df_ano['CATEGORIA CONSOLIDADA'].isin(categorias_selecionadas)]

    # Orçado: Previsto + Confirmado
    df_orcado = df_ano[
        df_ano['STATUS Consolidado'].isin(['Previsto', 'Confirmado'])
    ].groupby('MES_ORIGINAL')['VALOR'].sum().reset_index()
    df_orcado.columns = ['Mês', 'Orçado']

    # Realizado: Lançado
    df_realizado = df_ano[
        df_ano['STATUS Consolidado'] == 'Lançado'
    ].groupby('MES_ORIGINAL')['VALOR'].sum().reset_index()
    df_realizado.columns = ['Mês', 'Realizado']

    # Merge
    df_comparativo = pd.DataFrame({'Mês': range(1, 13)})
    df_comparativo = df_comparativo.merge(df_orcado, on='Mês', how='left').merge(df_realizado, on='Mês', how='left')
    df_comparativo = df_comparativo.fillna(0)
    df_comparativo['Mês Nome'] = df_comparativo['Mês'].map(meses_nome)
    df_comparativo['Diferença'] = df_comparativo['Realizado'] - df_comparativo['Orçado']

    # Gráfico barras agrupadas
    fig_comp = go.Figure()
    fig_comp.add_trace(go.Bar(
        x=df_comparativo['Mês Nome'],
        y=df_comparativo['Orçado'],
        name='Orçado (Previsto)',
        marker_color='#3498db',
        text=df_comparativo['Orçado'].apply(lambda x: f"R$ {x:,.0f}" if x > 0 else ""),
        textposition='outside'
    ))
    fig_comp.add_trace(go.Bar(
        x=df_comparativo['Mês Nome'],
        y=df_comparativo['Realizado'],
        name='Realizado (Lançado)',
        marker_color='#2ecc71',
        text=df_comparativo['Realizado'].apply(lambda x: f"R$ {x:,.0f}" if x > 0 else ""),
        textposition='outside'
    ))
    fig_comp.update_layout(barmode='group', height=350, legend=dict(orientation='h', yanchor='bottom', y=1.02))
    st.plotly_chart(fig_comp, use_container_width=True)

    # Tabela resumo
    df_tabela = df_comparativo[df_comparativo[['Orçado', 'Realizado']].sum(axis=1) > 0].copy()
    if not df_tabela.empty:
        df_tabela['% Desvio'] = df_tabela.apply(
            lambda r: (r['Diferença'] / r['Orçado'] * 100) if r['Orçado'] > 0 else 0, axis=1
        )
        st.dataframe(
            df_tabela[['Mês Nome', 'Orçado', 'Realizado', 'Diferença', '% Desvio']],
            column_config={
                'Mês Nome': st.column_config.TextColumn('Mês'),
                'Orçado': st.column_config.NumberColumn('Orçado', format='R$ %.2f'),
                'Realizado': st.column_config.NumberColumn('Realizado', format='R$ %.2f'),
                'Diferença': st.column_config.NumberColumn('Diferença', format='R$ %.2f'),
                '% Desvio': st.column_config.NumberColumn('% Desvio', format='%.1f%%'),
            },
            use_container_width=True,
            hide_index=True
        )
    
    # ==========================================
    # RODAPÉ
    # ==========================================
    
    st.markdown("---")
    st.caption("Dashboard Fluxo de Caixa - Newcore | Dados: Google Sheets + MySQL")


if __name__ == "__main__":
    main()
