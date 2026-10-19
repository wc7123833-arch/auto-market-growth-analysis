'use strict';
const fmt = new Intl.NumberFormat('zh-CN');
const signed = value => (value > 0 ? '+' : '') + fmt.format(value);
let portfolioData, period = 'FY2025';
const search = document.querySelector('#search');
function element(tag, text, cls) { const el = document.createElement(tag); if(text !== undefined) el.textContent = text; if(cls) el.className = cls; return el; }
function render() {
  if(!portfolioData) return;
  const all = portfolioData[period];
  const query = search.value.trim().toLocaleLowerCase();
  const rows = all.filter(r => (r.series_name + ' ' + r.brand).toLocaleLowerCase().includes(query)).sort((a,b)=>b.delta-a.delta || b.current_sales-a.current_sales);
  const current = all.reduce((n,r)=>n+r.current_sales,0), base = all.reduce((n,r)=>n+r.base_sales,0);
  document.querySelector('#period-label').textContent = period === 'FY2025' ? '2025 年 vs 2024 年' : '2026 年 1—6 月 vs 2025 年 1—6 月';
  document.querySelector('#market-total').textContent = `样本 MPV 总销量 ${fmt.format(current)} 辆 · 同比 ${current>=base?'+':''}${((current/base-1)*100).toFixed(2)}%`;
  const chart = document.querySelector('#growth-bars'); chart.replaceChildren();
  const top = rows.filter(r=>r.delta>0).slice(0,5), max = top[0]?.delta || 1;
  if(!top.length) chart.append(element('p', '当前筛选没有销量增长的车系。', 'small'));
  for(const row of top) {
    const wrapper = element('div', undefined, 'growth-row');
    wrapper.append(element('span',row.series_name));
    const track = element('div',undefined,'track'), fill = element('span',undefined,'fill');
    fill.style.setProperty('--size',`${row.delta/max*100}%`); track.append(fill);
    wrapper.append(track,element('span',signed(row.delta),'delta')); chart.append(wrapper);
  }
  const tbody = document.querySelector('#data-body'); tbody.replaceChildren();
  for(const row of rows.slice(0,10)) {
    const tr = element('tr'), title = element('td',row.series_name); title.append(element('small',row.brand));
    tr.append(title,element('td',fmt.format(row.current_sales)),element('td',fmt.format(row.base_sales)),element('td',signed(row.delta),row.delta>0?'positive':row.delta<0?'negative':''),element('td',row.yoy===null?'基期为零':`${row.yoy>0?'+':''}${(row.yoy*100).toFixed(1)}%`));
    tbody.append(tr);
  }
  if(!rows.length){const tr=element('tr'), td=element('td','没有匹配结果，请尝试其他车系或品牌。','empty');td.colSpan=5;tr.append(td);tbody.append(tr);}
  document.querySelector('#result-count').textContent = `匹配 ${rows.length} 个车系，展示 ${Math.min(10,rows.length)} 个。仅包含至少一个比较期有销量的车系；搜索不会改变样本总销量的统计范围。`;
}
document.querySelectorAll('[data-period]').forEach(button=>button.addEventListener('click',()=>{
  period=button.dataset.period;
  document.querySelectorAll('[data-period]').forEach(b=>{const active=b===button;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});render();
}));
search.addEventListener('input',render);
try {
  if(!window.AUTO_MARKET_DATA) throw Error('load');
  portfolioData=window.AUTO_MARKET_DATA;
  render();
} catch {
  document.querySelector('#market-total').textContent='数据暂时未能加载，请刷新页面，或直接下载 Excel / 完整项目。';
}
