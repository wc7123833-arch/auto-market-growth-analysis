# -*- coding: utf-8 -*-
"""汽车样本市场分析：可重复运行的 Python 数据处理、SQL 验证与图表流程。

默认离线读取已下载的数据。运行 --mysql 会连接专用数据库并验证 MySQL 结果。
原始 CSV 不修改；零值和异常值保留并加标记。详见项目说明与 Notebook。
"""
from pathlib import Path
import argparse, json, re, hashlib, datetime, os
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
RAW, CLEAN, RES, FIG = [ROOT / p for p in ['data/raw', 'data/processed', 'results', 'figures']]
for folder in [RAW, CLEAN, RES, FIG, ROOT/'sql']:
    folder.mkdir(parents=True, exist_ok=True)

def save(df, name, folder=RES):
    df.to_csv(folder/f'{name}.csv',index=False,encoding='utf-8-sig')
    return df

def write_json(obj, path):
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=lambda x: int(x) if isinstance(x,np.integer) else float(x) if isinstance(x,np.floating) else str(x),allow_nan=False),encoding='utf-8')

def key(value):
    return re.sub(r'\s+','',str(value)).casefold()

def load_and_audit():
    sales=pd.read_csv(RAW/'sales_filtered_24m.csv',encoding='utf-8-sig')
    config=pd.read_csv(RAW/'feature.csv',encoding='utf-8-sig')
    original=sales.copy()
    for col in ['brand','series_name','category']:
        sales[col]=sales[col].str.normalize('NFKC').str.strip()
    for col in ['year','month','series_id','monthly_sales']:
        sales[col]=pd.to_numeric(sales[col],errors='raise').astype('int64')
    sales['date']=pd.to_datetime(sales[['year','month']].assign(day=1))
    sales=sales.sort_values(['series_id','date']).reset_index(drop=True)
    duplicated=sales.duplicated(['series_id','date'],keep=False)
    save(sales[duplicated],'duplicate_sales')
    assert not duplicated.any(), '重复主键须人工裁决，不能静默删除'
    assert not sales[['series_id','date','monthly_sales','brand','series_name','category']].isna().any().any()
    assert sales.monthly_sales.ge(0).all()
    dim=sales[['series_id','series_name','brand','category']].drop_duplicates()
    assert dim.series_id.is_unique, '一车系存在多个品牌/类别，须人工核查'
    dim['name_key']=dim.series_name.map(key)
    assert dim.name_key.is_unique
    dim=dim.merge(sales.groupby('series_id').monthly_sales.sum().gt(0).rename('ever_positive'),on='series_id',validate='one_to_one')
    cal=pd.date_range(sales.date.min(),sales.date.max(),freq='MS')
    expected=pd.MultiIndex.from_product([dim.series_id,cal],names=['series_id','date'])
    missing=expected.difference(pd.MultiIndex.from_frame(sales[['series_id','date']])).to_frame(index=False)
    save(missing,'missing_months_global_grid')
    assert missing.empty, '当前指标脚本要求连续月份；新增数据有缺月时应先裁决'
    positive=sales.loc[sales.monthly_sales.gt(0)].groupby('series_id').date.agg(first_positive='min',last_positive='max')
    sales=sales.merge(positive,on='series_id',how='left',validate='many_to_one')
    sales['zero_position']=np.select([sales.monthly_sales.gt(0),sales.first_positive.isna(),sales.date.lt(sales.first_positive),sales.date.gt(sales.last_positive)],['positive','all_zero_series','before_first_positive','after_last_positive'],default='between_positive_months')
    sales['previous_month']=sales.groupby('series_id').monthly_sales.shift(1)
    change=sales.monthly_sales-sales.previous_month
    ratio=sales.monthly_sales/sales.previous_month.replace(0,np.nan)
    # 规则只是审查触发器，不表示错误。保留春节、上市爬坡等真实变动。
    sales['anomaly_flag']=((change.abs().ge(1000)) & ((sales.previous_month.ge(100)&(ratio.ge(3)|ratio.le(1/3))) | (sales.previous_month.eq(0)&sales.monthly_sales.ge(1000))))
    save(sales.loc[sales.anomaly_flag,['series_id','series_name','brand','date','monthly_sales','previous_month','zero_position']],'anomaly_review')
    save(sales.groupby(['date','category']).agg(series_count=('series_id','nunique'),positive_series=('monthly_sales',lambda x:x.gt(0).sum()),sales=('monthly_sales','sum')).reset_index(),'monthly_coverage')
    config['name_key']=config.series_name.str.normalize('NFKC').map(key)
    config['year']=pd.to_numeric(config.year,errors='raise').astype(int)
    assert not config.duplicated(['name_key','year']).any()
    config['config_series_id']=config.series_id.astype(str)
    config=config.drop(columns=['series_id']).merge(dim[['series_id','name_key','series_name']],on='name_key',how='left',suffixes=('_config',''),validate='many_to_one')
    config['name_model_year']=pd.to_numeric(config.car_name.str.extract(r'(20\d{2})\s*款',expand=False),errors='coerce')
    config['future_name_flag']=config.name_model_year.gt(config.year)
    config['mapping_status']=np.where(config.series_id.notna(),'normalized_name_match','unmatched')
    # ID 仅作交叉核验，不单独用来匹配：两个来源存在同值异车的 ID。
    config['source_pcauto_id']=pd.to_numeric(config.pcauto_series_id.astype(str).str.replace(r'^sg','',regex=True),errors='coerce')
    config['id_agrees']=config.source_pcauto_id.eq(config.series_id)
    save(config[['series_name_config','config_series_id','pcauto_series_id','series_id','series_name','year','mapping_status','id_agrees']],'config_mapping_audit')
    save(config.loc[config.future_name_flag,['series_name_config','year','name_model_year','car_name']],'config_future_year_flags')
    matched=config[config.series_id.notna()].copy();matched.series_id=matched.series_id.astype(int)
    assert not matched.duplicated(['series_id','year']).any()
    snapshot=matched.loc[matched.year.le(2026)].sort_values('year').drop_duplicates('series_id',keep='last')
    snapshot=snapshot.rename(columns={'year':'config_year'})
    assert snapshot.series_id.is_unique
    annual=sales.query('year==2025').groupby('series_id').monthly_sales.sum()
    counts={
        'sales_rows':len(sales),'series_count':len(dim),'brand_count':dim.brand.nunique(),
        'date_min':sales.date.min().strftime('%Y-%m'),'date_max':sales.date.max().strftime('%Y-%m'),
        'duplicate_rows':int(duplicated.sum()),'missing_grid_rows':len(missing),
        'missing_sales':int(original.monthly_sales.isna().sum()),'negative_sales':int(original.monthly_sales.lt(0).sum()),
        'zero_rows':int(sales.monthly_sales.eq(0).sum()),'all_zero_series':int((~dim.ever_positive).sum()),
        'active_series_ever':int(dim.ever_positive.sum()),'positive_rows':int(sales.monthly_sales.gt(0).sum()),
        'anomaly_flags':int(sales.anomaly_flag.sum()),'config_rows':len(config),
        'config_unique_series':config.name_key.nunique(),'config_matched_series':matched.series_id.nunique(),
        'config_future_name_rows':int(config.future_name_flag.sum()),
        'config_2025_sales_coverage':float(annual.loc[annual.index.isin(snapshot.series_id)].sum()/annual.sum()),
        'zero_positions':sales.zero_position.value_counts().to_dict(),
        'category_counts':dim.groupby('category').size().to_dict(),
        'mpv_series':int(dim.category.eq('MPV').sum()),
        'mpv_active_2025':int(sales.query("category=='MPV' and year==2025").groupby('series_id').monthly_sales.sum().gt(0).sum())}
    save(dim,'dim_series',CLEAN);save(sales,'fact_sales',CLEAN);save(matched,'config_matched',CLEAN);save(snapshot,'config_snapshot',CLEAN)
    write_json(counts,RES/'quality_summary.json')
    return sales,dim,matched,snapshot,counts

def source_checks(sales):
    # 2026-10-18 逐条查看同一平台公开页面并人工录入核验值；不是全量来源审计。
    entries=[
        ('Model 3','2022-01-01',2988,'https://www.pcauto.com.cn/jxwd/4132/41326406.html'),
        ('Model 3','2022-02-01',4607,'https://www.pcauto.com.cn/jxwd/4132/41326406.html'),
        ('Model 3','2024-06-01',18151,'https://m.pcauto.com.cn/x/5105/51055697.html'),
        ('Model 3','2025-12-01',27969,'https://m.pcauto.com.cn/x/5105/51055697.html'),
        ('别克GL8','2024-12-01',8877,'https://www.pcauto.com.cn/jxwd/5105/51055509.html'),
        ('别克GL8','2025-01-01',5980,'https://www.pcauto.com.cn/jxwd/5105/51055509.html'),
        ('别克GL8','2025-06-01',3830,'https://www.pcauto.com.cn/jxwd/5105/51055509.html'),
        ('别克GL8','2025-12-01',3852,'https://www.pcauto.com.cn/jxwd/5105/51055509.html'),
        ('岚图梦想家','2024-12-01',10116,'https://www.pcauto.com.cn/jxwd/5105/51055836.html'),
        ('岚图梦想家','2025-01-01',6071,'https://www.pcauto.com.cn/jxwd/5105/51055836.html'),
        ('岚图梦想家','2025-12-01',7008,'https://www.pcauto.com.cn/jxwd/5105/51055836.html'),
        ('腾势D9','2025-12-01',8439,'https://www.pcauto.com.cn/jxwd/5105/51054990.html'),
        ('魏牌 高山','2025-12-01',10558,'https://www.pcauto.com.cn/jxwd/5105/51054919.html')]
    checks=pd.DataFrame(entries,columns=['series_name','date','source_value','source_url'])
    checks.date=pd.to_datetime(checks.date)
    checks=checks.merge(sales[['series_name','date','monthly_sales']],on=['series_name','date'],how='left',validate='one_to_one')
    checks['matches']=checks.source_value.eq(checks.monthly_sales)
    checks['verified_on']='2026-10-18';checks['method']='同源页面人工抽查；定向样本，非随机抽样'
    save(checks,'source_spot_checks')
    return checks

def period_table(sales,group,period):
    # 对称时间窗，零基数同比设为空。
    old,new,months=(2024,2025,12) if period=='FY2025' else (2025,2026,6)
    g=sales.loc[sales.year.isin([old,new])&sales.month.le(months)].groupby(group+['year']).monthly_sales.sum().unstack('year')
    g=g.rename(columns={old:'base_sales',new:'current_sales'}).reset_index()
    assert g[['base_sales','current_sales']].notna().all().all()
    g['delta']=g.current_sales-g.base_sales
    g['yoy']=g.delta/g.base_sales.replace(0,np.nan)
    g['base_share']=g.base_sales/g.base_sales.sum();g['current_share']=g.current_sales/g.current_sales.sum()
    g['share_change_pp']=(g.current_share-g.base_share)*100
    g['base_rank']=g.base_sales.rank(method='dense',ascending=False).where(g.base_sales.gt(0))
    g['current_rank']=g.current_sales.rank(method='dense',ascending=False).where(g.current_sales.gt(0))
    g['rank_gain']=g.base_rank-g.current_rank
    g['comparison']=period
    return g

def analyze(sales,snapshot):
    out={}
    for scope,df in [('all',sales),('mpv',sales.loc[sales.category.eq('MPV')])]:
        for period in ['FY2025','H12026']:
            for entity,groups in [('brand',['brand']),('series',['series_id','series_name','brand'])]:
                table=period_table(df,groups,period)
                out[f'{scope}_{entity}_{period}']=save(table,f'{scope}_{entity}_{period}')
        # 先按品牌月聚合，再计算环比/同比；完成连续日历核验后 shift 才代表自然月。
        bm=df.groupby(['brand','date']).monthly_sales.sum().reset_index().sort_values(['brand','date'])
        bm['previous_month']=bm.groupby('brand').monthly_sales.shift(1)
        bm['previous_year']=bm.groupby('brand').monthly_sales.shift(12)
        bm['mom']=bm.monthly_sales/bm.previous_month.replace(0,np.nan)-1
        bm['yoy']=bm.monthly_sales/bm.previous_year.replace(0,np.nan)-1
        bm['share']=bm.monthly_sales/bm.groupby('date').monthly_sales.transform('sum')
        bm['rank']=bm.groupby('date').monthly_sales.rank(method='dense',ascending=False).where(bm.monthly_sales.gt(0))
        bm['rank_gain']=bm.groupby('brand')['rank'].shift(1)-bm['rank']
        out[f'{scope}_brand_month']=save(bm,f'{scope}_brand_month')
        for period in ['FY2025','H12026']:
            series=out[f'{scope}_series_{period}'].copy()
            series['brand_net_delta']=series.groupby('brand').delta.transform('sum')
            series['positive_delta']=series.delta.clip(lower=0)
            series['brand_positive_delta']=series.groupby('brand').positive_delta.transform('sum')
            series['positive_contribution']=series.positive_delta/series.brand_positive_delta.replace(0,np.nan)
            series['net_contribution']=series.delta/series.brand_net_delta.where(series.brand_net_delta.gt(0))
            save(series,f'{scope}_contribution_{period}');out[f'{scope}_contribution_{period}']=series
    mpv=sales.loc[sales.category.eq('MPV')].copy()
    mpv['prior_year']=mpv.groupby('series_id').monthly_sales.shift(12)
    rows=[]
    for sid,g in mpv.loc[mpv.year.eq(2025)].groupby('series_id'):
        diff=g.monthly_sales-g.prior_year
        rows.append({'series_id':sid,'series_name':g.series_name.iloc[0],'brand':g.brand.iloc[0],
                     'positive_yoy_months':int(diff.gt(0).sum()),'months':len(g),
                     'cv':float(g.monthly_sales.std(ddof=0)/g.monthly_sales.mean()) if g.monthly_sales.mean()>0 else np.nan,
                     'positive_months':int(g.monthly_sales.gt(0).sum()),
                     'q1_delta':float(diff[g.month.le(3)].sum()),'q2_delta':float(diff[g.month.between(4,6)].sum()),
                     'q3_delta':float(diff[g.month.between(7,9)].sum()),'q4_delta':float(diff[g.month.ge(10)].sum())})
    stability=pd.DataFrame(rows).merge(out['mpv_series_FY2025'],on=['series_id','series_name','brand'],validate='one_to_one')
    out['mpv_stability']=save(stability,'mpv_stability')
    # 每年按品牌累计销量重新选取头部；CR3/CR5 使用固定数量，不因并列增加成员。
    conc=[]
    for year,g in mpv.query('year<=2025').groupby('year'):
        by=g.groupby('brand').monthly_sales.sum().sort_values(ascending=False)
        shares=by/by.sum()
        conc.append({'year':year,'sales':int(by.sum()),'active_brands':int(by.gt(0).sum()),'cr3':float(shares.head(3).sum()),'cr5':float(shares.head(5).sum()),'hhi':float((shares**2).sum()*10000)})
    out['mpv_concentration']=save(pd.DataFrame(conc),'mpv_concentration')
    annual=sales.groupby(['category','year']).monthly_sales.sum().unstack('year').reset_index()
    out['category_annual']=save(annual,'category_annual')
    # 配置快照仅用于产品画像，不能宣称为历史时点可获得的配置。
    cols=['series_id','config_year','series_name_config','car_name','energy_type','vehicle_class','official_price_wan','wheelbase_mm','seat_count','name_model_year','future_name_flag']
    product=out['mpv_series_FY2025'].merge(snapshot[cols],on='series_id',how='left',validate='one_to_one')
    assert product.current_sales.sum()==out['mpv_series_FY2025'].current_sales.sum()
    out['mpv_product_snapshot']=save(product,'mpv_product_snapshot')
    # 固定 2025 下半年有正销量的 MPV 车系，避免看过测试期后选样本。
    eligible=mpv.loc[mpv.year.eq(2025)&mpv.month.ge(7)].groupby('series_id').monthly_sales.sum()
    eligible=eligible[eligible.gt(0)].index
    forecasts=mpv.loc[mpv.series_id.isin(eligible)].sort_values(['series_id','date']).copy()
    forecasts['last_month']=forecasts.groupby('series_id').monthly_sales.shift(1)
    forecasts['mean_3']=forecasts.groupby('series_id').monthly_sales.transform(lambda x:x.shift(1).rolling(3,min_periods=3).mean())
    test=forecasts.loc[forecasts.year.eq(2026)&forecasts.month.le(6),['series_id','series_name','brand','date','monthly_sales','last_month','mean_3']]
    assert len(test)==len(eligible)*6 and test[['last_month','mean_3']].notna().all().all()
    fm=[]
    for model in ['last_month','mean_3']:
        for month,g in [('ALL',test)]+[(d.strftime('%Y-%m'),g) for d,g in test.groupby('date')]:
            err=(g[model]-g.monthly_sales).abs()
            fm.append({'model':model,'month':month,'n':len(g),'series_count':g.series_id.nunique(),'mae':float(err.mean()),'wmape':float(err.sum()/g.monthly_sales.sum()),'bias':float((g[model]-g.monthly_sales).sum()/g.monthly_sales.sum())})
    out['forecast_metrics']=save(pd.DataFrame(fm),'forecast_metrics');out['forecast_predictions']=save(test,'forecast_predictions')
    return out

def run_mysql(sales,dim,config,expected):
    import pymysql
    conn=pymysql.connect(host=os.getenv('AUTO_MYSQL_HOST','127.0.0.1'),port=int(os.getenv('AUTO_MYSQL_PORT','3308')),user=os.getenv('AUTO_MYSQL_USER','root'),password=os.getenv('AUTO_MYSQL_PASSWORD',''),charset='utf8mb4',autocommit=True)
    schema=(ROOT/'sql/analysis.sql').read_text(encoding='utf-8')
    cur=conn.cursor()
    for statement in re.sub(r'--[^\n]*','',schema).split(';'):
        if statement.strip():cur.execute(statement)
    dims=dim[['series_id','series_name','brand','category']].values.tolist()
    cur.executemany('INSERT INTO dim_series VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE series_name=VALUES(series_name),brand=VALUES(brand),category=VALUES(category)',dims)
    rows=[(int(r.series_id),r.date.date(),int(r.monthly_sales),str(r.data_source)) for r in sales.itertuples()]
    for start in range(0,len(rows),3000):
        cur.executemany('INSERT INTO fact_sales VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE monthly_sales=VALUES(monthly_sales),data_source=VALUES(data_source)',rows[start:start+3000])
    cr=config[['series_id','year','energy_type','vehicle_class','official_price_wan','wheelbase_mm','car_name','future_name_flag']].astype(object).where(pd.notna(config[['series_id','year','energy_type','vehicle_class','official_price_wan','wheelbase_mm','car_name','future_name_flag']]),None)
    cur.executemany('INSERT INTO config_year VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE energy_type=VALUES(energy_type),vehicle_class=VALUES(vehicle_class),official_price_wan=VALUES(official_price_wan),wheelbase_mm=VALUES(wheelbase_mm),car_name=VALUES(car_name),future_name_flag=VALUES(future_name_flag)',cr.values.tolist())
    views=['v_brand_month','v_series_month','v_brand_period','v_series_period','v_series_contribution','v_mpv_concentration','v_product_snapshot']
    results={}
    for view in views:
        cur.execute('SELECT * FROM '+view)
        table=pd.DataFrame(cur.fetchall(),columns=[x[0] for x in cur.description])
        for col in table.columns:
            if col not in ['scope','brand','series_name','category','comparison','date','data_source','energy_type','vehicle_class','car_name']:
                table[col]=pd.to_numeric(table[col],errors='raise')
        save(table,'mysql_'+view);results[view]=table
    checks=[]
    for scope,label in [('all','全样本'),('mpv','MPV')]:
        for period in ['FY2025','H12026']:
            py=expected[f'{scope}_brand_{period}']
            sq=results['v_brand_period'].query('scope==@label and comparison==@period')
            joined=py.merge(sq,on='brand',suffixes=('_py','_sql'),validate='one_to_one')
            assert len(joined)==len(py)==len(sq)
            for col in ['base_sales','current_sales','delta','yoy','base_share','current_share','share_change_pp','base_rank','current_rank','rank_gain']:
                a=joined[col+'_py'].astype(float);b=joined[col+'_sql'].astype(float)
                assert np.allclose(a,b,atol=1e-6,rtol=1e-7,equal_nan=True),(scope,period,col)
                checks.append({'scope':label,'comparison':period,'metric':col,'rows':len(joined),'passed':True})
        py=expected[f'{scope}_brand_month'].copy();sq=results['v_brand_month'].query('scope==@label').copy();sq.date=pd.to_datetime(sq.date)
        j=py.merge(sq,on=['brand','date'],suffixes=('_py','_sql'),validate='one_to_one')
        assert len(j)==len(py)==len(sq)
        for col in ['monthly_sales','mom','yoy','share','rank_gain']:
            assert np.allclose(j[col+'_py'].astype(float),j[col+'_sql'].astype(float),atol=1e-6,rtol=1e-7,equal_nan=True),col
            checks.append({'scope':label,'comparison':'monthly','metric':col,'rows':len(j),'passed':True})
    cur.execute('SELECT COUNT(*),SUM(monthly_sales) FROM fact_sales');count,total=cur.fetchone()
    assert count==len(sales) and int(total)==sales.monthly_sales.sum()
    cur.execute('SELECT VERSION()');version=cur.fetchone()[0]
    save(pd.DataFrame(checks),'mysql_python_reconciliation')
    write_json({'engine':'MySQL','version':version,'database':'auto_market_learning_20261018','fact_rows':count,'total_sales':int(total),'checks':len(checks),'all_passed':True,'executed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},RES/'mysql_validation.json')
    conn.close()

def charts(sales,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    font=Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white','axes.titleweight':'bold'})
    palette=['#24466B','#2D8B87','#E39E36','#9D5B71','#71869D','#717C39']
    def done(fig,name):
        fig.tight_layout();fig.savefig(FIG/f'{name}.png',dpi=160,bbox_inches='tight');plt.close(fig)
    fig,ax=plt.subplots(figsize=(9.3,3.6))
    d=sales.groupby(['date','category']).monthly_sales.sum().unstack('category')/10000
    d.plot(ax=ax,color=palette[:3]);ax.set(xlabel='',ylabel='月销量（万辆）',title='样本三类产品销量走势');ax.legend(title='');done(fig,'01_category_trend')
    fig,ax=plt.subplots(figsize=(9.3,4))
    d=out['all_brand_FY2025'].nlargest(10,'delta').sort_values('delta')
    ax.barh(d.brand,d.delta/10000,color=palette[0]);ax.set(xlabel='2025 年较 2024 年销量增量（万辆）',title='样本品牌增长贡献前十');done(fig,'02_brand_growth')
    fig,ax=plt.subplots(figsize=(9.3,4.2))
    d=out['mpv_series_FY2025'];d=pd.concat([d.nlargest(5,'delta'),d.nsmallest(5,'delta')]).sort_values('delta')
    ax.barh(d.series_name,d.delta/10000,color=[palette[1] if v>0 else palette[3] for v in d.delta]);ax.axvline(0,color='#555',lw=.7);ax.set(xlabel='2025 年较 2024 年增减量（万辆）',title='MPV 车系增量与拖累：各取前五');done(fig,'03_mpv_growth')
    names=['魏牌 高山','岚图梦想家','腾势D9','别克GL8','别克GL8新能源']
    fig,ax=plt.subplots(figsize=(9.3,4.1))
    d=sales.loc[sales.series_name.isin(names)&sales.year.ge(2024)].pivot(index='date',columns='series_name',values='monthly_sales')/1000
    d.plot(ax=ax,color=palette);ax.set(xlabel='',ylabel='月销量（千辆）',title='MPV 重点车系：增长节奏与近期变化');ax.legend(ncol=3,fontsize=8,loc='upper center');done(fig,'04_mpv_trend')
    fig,ax=plt.subplots(figsize=(9.3,3.3))
    d=out['mpv_concentration'];ax.plot(d.year,d.cr3*100,'o-',label='品牌 CR3',color=palette[0]);ax.plot(d.year,d.cr5*100,'o-',label='品牌 CR5',color=palette[1]);ax.set(xticks=d.year,ylabel='样本 MPV 销量占比（%）',title='MPV 头部品牌集中度');ax.legend();done(fig,'05_concentration')
    fig,ax=plt.subplots(figsize=(9.3,3.3))
    d=out['forecast_metrics'].query("month!='ALL'")
    for model,name,color in [('last_month','沿用上月销量',palette[0]),('mean_3','近三个月均值',palette[2])]:
        g=d.query('model==@model');ax.plot(g.month,g.wmape*100,'o-',label=name,color=color)
    ax.set(ylabel='WMAPE（%）',title='MPV 滚动单月预测：2026 年上半年');ax.legend();done(fig,'06_forecast')

def main(use_mysql=False):
    sales,dim,config,snapshot,quality=load_and_audit()
    checks=source_checks(sales);quality['spot_checks']=len(checks);quality['spot_matches']=int(checks.matches.sum())
    out=analyze(sales,snapshot)
    if use_mysql:run_mysql(sales,dim,config,out)
    charts(sales,out)
    write_json(quality,RES/'quality_summary.json')
    print(json.dumps(quality,ensure_ascii=False,indent=2))
    print('分析完成：',ROOT)
    return sales,out,quality

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mysql',action='store_true');args=parser.parse_args()
    main(args.mysql)
