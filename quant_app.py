import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import concurrent.futures
import requests
from io import StringIO
import plotly.express as px
import plotly.graph_objects as go

st.set_page_config(page_title="한국 주식 퀀트 플랫폼", page_icon="📈", layout="wide")

st.title("💡 K-퀀트 인텔리전스 (백테스팅 & 스크리닝)")
st.markdown("듀얼 모멘텀(절대/상대)과 기관 수급 데이터를 결합하여 검증된 퀀트 투자 전략을 실행합니다.")

tab1, tab2 = st.tabs(["🚀 실시간 종목 스크리닝 (스크리너)", "📊 과거 시뮬레이션 (백테스터)"])

# ----------------- 캐싱된 데이터 수집 함수 -----------------
@st.cache_data(show_spinner=False, ttl=3600*24)
def get_price_data(code, start_date_str, end_date_str):
    try:
        return fdr.DataReader(code, start_date_str, end_date_str)
    except:
        return None

@st.cache_data(show_spinner=False, ttl=3600*24)
def get_supply_data(code, target_date_str):
    headers = {'User-Agent': 'Mozilla/5.0', 'Connection': 'close'}
    inst_10d, frgn_10d = 0, 0
    try:
        all_dfs = []
        for page in [1, 2]:
            res = requests.get(f'https://finance.naver.com/item/frgn.naver?code={code}&page={page}', headers=headers, timeout=2)
            dfs = pd.read_html(StringIO(res.text), encoding='euc-kr')
            if len(dfs) > 3:
                df = dfs[3].dropna()
                all_dfs.append(df)
        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
            combined_df['date_dt'] = pd.to_datetime(combined_df.iloc[:, 0], format='%Y.%m.%d', errors='coerce')
            valid_df = combined_df[combined_df['date_dt'] <= pd.to_datetime(target_date_str)]
            
            if not valid_df.empty:
                idx = valid_df.index[0]
                df_10d = valid_df.loc[idx:idx+9]
                if len(df_10d) > 0:
                    inst_10d = int(df_10d.iloc[:, 5].astype(float).sum())
                    frgn_10d = int(df_10d.iloc[:, 6].astype(float).sum())
    except: pass
    return inst_10d, frgn_10d

# ==============================================================================
# 탭 1: 스크리닝 (듀얼 모멘텀 개별주 + 수급)
# ==============================================================================
with tab1:
    st.header("🔍 개별주 듀얼 모멘텀 & 수급 스크리너")
    st.markdown("최근 N개월간 지수보다 강한 상승세(상대 모멘텀)를 보이고, 절대적으로 상승 추세(절대 모멘텀)에 있으면서 기관 매집이 일어난 주도주를 찾습니다.")
    
    with st.expander("📖 실전 매매 가이드 (언제 사고, 언제 파는가?)", expanded=True):
        st.markdown("""
        #### 🟢 매수 (Buy) 타이밍
        1. **매일 장 마감 후**에 이 스크리너를 실행하여 시장의 주도주 흐름과 수급을 체크합니다.
        2. 스크리닝을 통과하여 하단 표에 나타난 최상위 주도주들을 **동일 비중으로 분할 매수**합니다.
        3. 만약 검색되는 종목이 아예 없다면(하락장), 절대 주식을 사지 않고 **현금 비중을 유지**합니다. (절대 모멘텀 작동)

        #### 🔴 매도 (Sell) 타이밍
        본인의 투자 호흡에 맞춰 **2주, 2개월, 3개월 단위** 중 하나의 매도 규칙을 선택하여 실행합니다.
        1. **[단기 전략 - 2주 단위]**: 매수 후 2주 뒤 스크리너를 확인하여 리스트에서 탈락했다면 즉시 전량 매도 (빠른 모멘텀/수급 이탈 방어)
        2. **[중기 전략 - 2개월 단위]**: 매수 후 2개월 뒤 확인하여 중기 추세가 꺾인 종목만 솎아내어 교체
        3. **[장기 전략 - 3개월 단위]**: 실적 발표 주기(분기)인 3개월 단위로만 평가하여, 모멘텀이 꺾이지 않은 찐 주도주는 끝까지 홀딩하며 수익 극대화
        
        * ❌ **매도 원칙**: 정해둔 단위 기간(2주/2개월/3개월)이 지난 시점에 스크리너에서 **종목이 사라졌다면 뒤도 돌아보지 않고 기계적으로 매도**합니다.
        * 🔄 **보유 원칙**: 정해둔 기간이 지났어도 종목이 **여전히 상위권에 살아있다면 절대 팔지 않고 계속 보유(Hold)** 하여 추세의 끝까지 먹습니다.
        """)
        
    colA, colB, colC = st.columns(3)
    with colA:
        scr_target_date = st.date_input("🗓️ 스크리닝 기준일", value=datetime.today())
    with colB:
        momentum_period = st.selectbox("📈 상대 모멘텀 기준 (개월)", [3, 6, 12], index=1)
    with colC:
        min_inst_buy = st.slider("🏦 최소 기관 순매수 (10일 누적, 주)", 0, 500000, 50000, step=10000)

    def process_screening(code, name, market, sector, target_date_dt, start_date_dt, kospi_ret):
        try:
            df = get_price_data(code, start_date_dt.strftime('%Y-%m-%d'), target_date_dt.strftime('%Y-%m-%d'))
            if df is None or len(df) < 20: return None
            
            current_price = int(df['Close'].iloc[-1])
            past_price = df['Close'].iloc[0]
            stock_ret = ((current_price - past_price) / past_price) * 100
            
            # 절대 모멘텀: 주식이 상승했는가? (예: 수익률 > 0)
            if stock_ret <= 0: return None
            
            # 상대 모멘텀: 시장 지수(KOSPI)보다 많이 올랐는가?
            if stock_ret < kospi_ret: return None
            
            # 수급 데이터
            inst_10d, frgn_10d = get_supply_data(code, target_date_dt.strftime('%Y-%m-%d'))
            
            if inst_10d >= min_inst_buy:
                return {
                    '종목코드': code,
                    '종목명': name,
                    '시장': market,
                    '업종': sector,
                    f'{momentum_period}개월 수익률(%)': round(stock_ret, 2),
                    '초과수익률(vs KOSPI)': round(stock_ret - kospi_ret, 2),
                    '기관순매수(10일)': inst_10d,
                    '외인순매수(10일)': frgn_10d
                }
        except: pass
        return None

    if st.button("🚀 주도주 스크리닝 시작"):
        target_date_dt = pd.to_datetime(scr_target_date)
        start_date_dt = target_date_dt - relativedelta(months=momentum_period)
        
        with st.spinner("코스피 지수 수익률을 계산 중입니다..."):
            kospi_df = get_price_data('KS11', start_date_dt.strftime('%Y-%m-%d'), target_date_dt.strftime('%Y-%m-%d'))
            kospi_ret = 0.0
            if kospi_df is not None and len(kospi_df) > 0:
                kospi_ret = ((kospi_df['Close'].iloc[-1] - kospi_df['Close'].iloc[0]) / kospi_df['Close'].iloc[0]) * 100
            st.info(f"👉 기준기간 KOSPI 지수 수익률: {kospi_ret:.2f}% (이 지수보다 수익률이 높아야 통과)")

        with st.spinner("전 종목 듀얼 모멘텀 및 수급 분석 중... (약 1분 소요)"):
            try:
                krx = pd.read_csv('tickers.csv', dtype=str)
            except:
                krx = fdr.StockListing('KRX-DESC')
            if 'Market' in krx.columns:
                krx = krx[krx['Market'].isin(['KOSPI', 'KOSDAQ'])]
                
            results = []
            target_stocks = krx.to_dict('records')
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            completed = 0
            total = len(target_stocks)
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=40) as executor:
                futures = {executor.submit(process_screening, row['Code'], row['Name'], row.get('Market','-'), row.get('Sector','-'), target_date_dt, start_date_dt, kospi_ret): row for row in target_stocks}
                for future in concurrent.futures.as_completed(futures):
                    completed += 1
                    if completed % 20 == 0 or completed == total:
                        progress_bar.progress(completed / total)
                        status_text.text(f"분석 중... ({completed}/{total})")
                    
                    res = future.result()
                    if res is not None:
                        results.append(res)
            
            progress_bar.progress(1.0)
            status_text.text("스크리닝 완료!")
            
            if results:
                res_df = pd.DataFrame(results)
                # 정렬: 상대 모멘텀 수익률 1순위, 기관수급 2순위
                res_df = res_df.sort_values(by=[f'{momentum_period}개월 수익률(%)', '기관순매수(10일)'], ascending=[False, False])
                for col in ['기관순매수(10일)', '외인순매수(10일)']:
                    res_df[col] = res_df[col].apply(lambda x: f"{x:,}")
                st.success(f"🎉 듀얼 모멘텀 & 수급 조건을 만족하는 종목 {len(res_df)}개를 찾았습니다!")
                st.dataframe(res_df, use_container_width=True)
            else:
                st.warning("조건을 만족하는 종목이 없습니다.")

# ==============================================================================
# 탭 2: 백테스팅 (섹터 ETF 듀얼 모멘텀)
# ==============================================================================
with tab2:
    st.header("📊 섹터 로테이션 듀얼 모멘텀 백테스터")
    st.markdown("개별주 2000개를 수십 년 백테스트하는 것은 매우 느리므로, **국내 대표 섹터 ETF**들을 활용하여 듀얼 모멘텀 전략의 장기 파괴력을 직접 시뮬레이션합니다.")
    
    st.info("💡 **전략 로직**: 매월 말 각 섹터의 최근 N개월 수익률(상대 모멘텀)을 평가합니다. 가장 수익률이 높은 1개 섹터에 100% 투자하되, 그 섹터조차 예금(국고채)보다 수익률이 낮다면(절대 모멘텀 실패) 100% 현금(국고채)으로 도망가 폭락을 방어합니다.")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        backtest_years = st.slider("🗓️ 백테스트 기간 (최근 N년)", 1, 10, 5)
    with col2:
        lookback_months = st.selectbox("⏱️ 모멘텀 측정 기간", [1, 3, 6, 12], index=1)
    with col3:
        initial_capital = st.number_input("💰 초기 투자금 (원)", value=10000000, step=1000000)

    # 대표 섹터 ETF 코드 (KODEX 200, 코스닥150, 반도체, 자동차, 헬스케어, 국고채3년)
    etf_pool = {
        '069500': 'KODEX 200 (시장대표)',
        '229200': 'KODEX 코스닥150 (중소형)',
        '091160': 'KODEX 반도체',
        '091180': 'KODEX 자동차',
        '261220': 'KODEX 헬스케어',
        '114260': 'KODEX 국고채3년 (안전/현금)'
    }
    safe_asset_code = '114260' # 국고채를 현금(무위험자산) 대용으로 사용

    if st.button("⚙️ 듀얼 모멘텀 백테스트 실행"):
        end_date = datetime.today()
        # 데이터는 룩백 기간 + 백테스트 기간을 위해 넉넉히 다운
        start_date = end_date - relativedelta(years=backtest_years) - relativedelta(months=lookback_months+1)
        
        with st.spinner("ETF 과거 데이터를 수집하고 시뮬레이션 중입니다... (약 10초)"):
            prices = pd.DataFrame()
            for code, name in etf_pool.items():
                pdf = get_price_data(code, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
                if pdf is not None and not pdf.empty:
                    prices[code] = pdf['Close']
            
            prices = prices.ffill().dropna()
            
            # 월말 데이터만 추출 (리밸런싱은 매월 말 1회 진행)
            monthly_prices = prices.resample('ME').last()
            
            portfolio_values = []
            current_capital = initial_capital
            
            dates = monthly_prices.index
            # 시뮬레이션 시작 인덱스 (룩백 기간 이후부터 시작)
            start_idx = lookback_months
            
            portfolio_history = []
            
            for i in range(start_idx, len(dates)-1):
                today_date = dates[i]
                next_date = dates[i+1]
                lookback_date = dates[i - lookback_months]
                
                # 1. 룩백 기간 동안의 수익률 계산 (모멘텀 측정)
                momentum_returns = {}
                for code in etf_pool.keys():
                    if code == safe_asset_code: continue # 안전자산은 상대모멘텀 비교에서 제외
                    try:
                        ret = (monthly_prices.loc[today_date, code] - monthly_prices.loc[lookback_date, code]) / monthly_prices.loc[lookback_date, code]
                        momentum_returns[code] = ret
                    except: pass
                
                # 안전자산 수익률 (절대 모멘텀 기준점)
                safe_ret = (monthly_prices.loc[today_date, safe_asset_code] - monthly_prices.loc[lookback_date, safe_asset_code]) / monthly_prices.loc[lookback_date, safe_asset_code]
                
                # 2. 가장 모멘텀이 높은 자산 선택 (상대 모멘텀)
                best_asset = max(momentum_returns, key=momentum_returns.get)
                best_ret = momentum_returns[best_asset]
                
                # 3. 절대 모멘텀 검증: 최고 자산의 수익률이 안전자산(국고채) 수익률보다 낮으면 국고채로 도피
                target_asset = best_asset if best_ret > safe_ret else safe_asset_code
                
                # 4. 다음 달까지 보유했을 때의 실현 수익률 (투자)
                realized_ret = (monthly_prices.loc[next_date, target_asset] - monthly_prices.loc[today_date, target_asset]) / monthly_prices.loc[today_date, target_asset]
                
                current_capital = current_capital * (1 + realized_ret)
                
                portfolio_history.append({
                    'Date': next_date,
                    'Portfolio_Value': current_capital,
                    'Holdings': etf_pool[target_asset],
                    'Return_1M': realized_ret * 100
                })
            
            # KODEX 200 (시장) 단순 보유(Buy and Hold) 시뮬레이션 비교
            market_start_price = monthly_prices.loc[dates[start_idx], '069500']
            market_values = []
            market_cap = initial_capital
            for i in range(start_idx, len(dates)-1):
                next_date = dates[i+1]
                cur_price = monthly_prices.loc[next_date, '069500']
                market_values.append({
                    'Date': next_date,
                    'Market_Value': initial_capital * (cur_price / market_start_price)
                })
                
            # 결과 병합
            pf_df = pd.DataFrame(portfolio_history).set_index('Date')
            mk_df = pd.DataFrame(market_values).set_index('Date')
            res_df = pf_df.join(mk_df)
            
            # 성과 지표 계산
            final_portfolio = res_df['Portfolio_Value'].iloc[-1]
            final_market = res_df['Market_Value'].iloc[-1]
            
            pf_total_return = ((final_portfolio / initial_capital) - 1) * 100
            mk_total_return = ((final_market / initial_capital) - 1) * 100
            
            cagr = ((final_portfolio / initial_capital) ** (1/backtest_years) - 1) * 100
            
            # MDD (최대 낙폭)
            res_df['Peak'] = res_df['Portfolio_Value'].cummax()
            res_df['Drawdown'] = (res_df['Portfolio_Value'] - res_df['Peak']) / res_df['Peak'] * 100
            mdd = res_df['Drawdown'].min()
            
            mk_peak = res_df['Market_Value'].cummax()
            mk_dd = (res_df['Market_Value'] - mk_peak) / mk_peak * 100
            mk_mdd = mk_dd.min()
            
            # 결과 화면 출력
            st.subheader("🏆 백테스트 최종 성과 요약")
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("최종 자산", f"{int(final_portfolio):,}원", f"초기 {int(initial_capital):,}원")
            col2.metric("누적 수익률", f"{pf_total_return:.2f}%", f"시장(KODEX200): {mk_total_return:.2f}%")
            col3.metric("연평균 수익률(CAGR)", f"{cagr:.2f}%")
            col4.metric("최대 낙폭(MDD)", f"{mdd:.2f}%", f"시장 MDD: {mk_mdd:.2f}%")
            
            st.markdown("---")
            st.subheader("📈 자산 성장 곡선 (듀얼 모멘텀 vs 시장수익률)")
            fig = px.line(res_df.reset_index(), x='Date', y=['Portfolio_Value', 'Market_Value'], 
                          labels={'value': '자산 가치 (원)', 'variable': '전략'},
                          color_discrete_map={'Portfolio_Value': '#00d26a', 'Market_Value': '#555555'})
            fig.update_layout(hovermode="x unified", legend_title_text='')
            newnames = {'Portfolio_Value': '듀얼 모멘텀 포트폴리오', 'Market_Value': 'KODEX 200 (단순보유)'}
            fig.for_each_trace(lambda t: t.update(name = newnames[t.name],
                                                legendgroup = newnames[t.name],
                                                hovertemplate = t.hovertemplate.replace(t.name, newnames[t.name])))
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("📝 월별 리밸런싱 기록 (최근 12개월)")
            st.dataframe(res_df[['Holdings', 'Return_1M']].tail(12).sort_index(ascending=False), use_container_width=True)
