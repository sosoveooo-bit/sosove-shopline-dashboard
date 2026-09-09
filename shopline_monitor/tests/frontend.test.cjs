const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function appContext() {
  const nodes = new Map();
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      dataset: {}, value: '', options: [], textContent: '', hidden: false, disabled: false,
      classList: { toggle() {}, add() {}, remove() {} },
      setAttribute() {}, addEventListener() {}, showModal() { this.open = true; }, close() { this.open = false; },
    });
    return nodes.get(id);
  };
  const context = vm.createContext({
    document: { documentElement: { dataset: {} }, addEventListener() {}, querySelectorAll: () => [], getElementById: node },
    localStorage: { getItem: () => null }, sessionStorage: { getItem: () => null },
    window: { setTimeout, clearTimeout }, AbortController, URLSearchParams,
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
  return { run: (script) => vm.runInContext(script, context), node };
}

test('merged Yahoo pagination retains all 20 orders while raw Yahoo stays separate', () => {
  const { run, node } = appContext();
  run(`
    const orders = Array.from({length:20}, (_, i) => ({id:String(i), total:100, status:'paid'}));
    state.payload = {currency:'JPY', channels:[{channel:'Yahoo',orders:2,orderDetails:orders.slice(0,2)}],
      focusChannels:[{channel:'Yahoo',orders:20,orderDetails:orders}]};
    openChannelDialog(findChannelRow('Yahoo', true), 'JPY', true);
    changeChannelDialogPage(1);
  `);
  assert.match(node('channel-dialog-count').textContent, /11.*20 \/ 20/);
  assert.match(node('channel-dialog-order-rows').innerHTML, /title="19"/);
  run(`openChannelDialog(findChannelRow('Yahoo'), 'JPY');`);
  assert.match(node('channel-dialog-count').textContent, /1.*2 \/ 2/);
});

test('SmartPush summary opens matching orders, supports paging and the order shortcut', () => {
  const { run, node } = appContext();
  node('order-source-filter').options = [{value:'SmartPush'}, {value:'Email'}];
  run(`
    const orders = Array.from({length:12}, (_, i) => ({id:'sp-'+i, source:'SmartPush', total:100, status:'paid', utmSource:'email', utmMedium:'smartpush'}));
    state.payload = {currency:'JPY', orders:[...orders, {id:'email-only',source:'Email'}],
      channels:[{channel:'SmartPush',orders:12,officialOrders:12,revenue:1200,orderDetails:orders}],
      channelAnalytics:{smartPushOrders:12,smartPushRevenue:1200}, focusChannels:[]};
    renderManagedTable=() => {};
    renderChannels(state.payload.channels, 'JPY', state.payload.channelAnalytics);
    const button = {dataset:{channelDrill:'SmartPush'},classList:{contains:() => false}};
    handleChannelPanelAction({target:{closest:() => button}});
  `);
  assert.equal(node('channel-order-dialog').open, true);
  assert.equal(node('channel-dialog-title').textContent, 'SmartPush 订单核对');
  assert.equal(node('channel-dialog-orders').textContent, node('channel-smartpush-orders').textContent);
  assert.equal(node('channel-dialog-revenue').textContent, node('channel-smartpush-revenue').textContent);
  run('changeChannelDialogPage(1)');
  assert.match(node('channel-dialog-count').textContent, /11.*12 \/ 12/);
  assert.match(node('channel-dialog-order-rows').innerHTML, /smartpush/);
  run('renderOrders=() => {}; focusPanel=() => {}; resetTablePage=() => {}; filterChannelOrders();');
  assert.equal(run('filterOrders(state.payload.orders).length'), 12);
  assert.equal(run('state.orderSource'), 'SmartPush');
});

test('SmartPush summary with no orders shows an empty dialog without paging', () => {
  const { run, node } = appContext();
  run(`
    state.payload = {currency:'JPY', channels:[], focusChannels:[]};
    const button = {dataset:{channelDrill:'SmartPush'},classList:{contains:() => false}};
    handleChannelPanelAction({target:{closest:() => button}});
  `);
  assert.equal(node('channel-dialog-orders').textContent, '0 单');
  assert.equal(node('channel-dialog-count').textContent, '当前来源暂无订单');
  assert.equal(node('channel-dialog-prev').disabled, true);
  assert.equal(node('channel-dialog-next').disabled, true);
});

test('focus order shortcut filters by group membership, not just source Yahoo', () => {
  const { run, node } = appContext();
  node('order-source-filter').options = [{value:'Yahoo'}, {value:'focus:Yahoo'}];
  const ids = run(`
    state.payload = {currency:'JPY', orders:[{id:'y',source:'Yahoo'},{id:'g',source:'Google'},{id:'ad',source:'Google'}],
      channels:[{channel:'Yahoo',orders:1,orderDetails:[{id:'y'}]}],
      focusChannels:[{channel:'Yahoo',orders:2,focusSummary:'Yahoo + Google',orderDetails:[{id:'y'},{id:'g'}]}]};
    renderOrders = () => {}; focusPanel = () => {}; resetTablePage = () => {};
    openChannelDialog(findChannelRow('Yahoo', true), 'JPY', true);
    filterChannelOrders();
    filterOrders(state.payload.orders).map(order => order.id).join(',');
  `);
  assert.equal(ids, 'y,g');
});

test('Today follows the configured store midnight', () => {
  const { run, node } = appContext();
  const changed = run(`
    state.storeTimezone = 'Asia/Tokyo'; state.followToday = true; state.range = '1d'; state.date = '2026-09-07';
    refreshTodaySelection(new Date('2026-09-07T15:01:00Z'));
  `);
  assert.equal(changed, true);
  assert.equal(run('state.date'), '2026-09-08');
  assert.equal(node('date-picker').max, '2026-09-08');
});

test('manual dates including a manually selected today do not roll over', () => {
  const { run } = appContext();
  const changed = run(`
    state.storeTimezone = 'Asia/Shanghai'; state.followToday = false; state.range = '1d'; state.date = '2026-09-07';
    refreshTodaySelection(new Date('2026-09-07T16:01:00Z'));
  `);
  assert.equal(changed, false);
  assert.equal(run('state.date'), '2026-09-07');
});

test('order colors use exact states and bad or pending states take precedence', () => {
  const { run } = appContext();
  for (const status of ['unpaid', 'unfulfilled', 'pending', 'partially_paid']) {
    assert.equal(run(`statusTone({status:'${status}'})`), 'warn', status);
  }
  assert.equal(run("statusTone({status:'paid', fulfillmentStatus:'unfulfilled'})"), 'warn');
  assert.equal(run("statusTone({status:'unpaid', fulfillmentStatus:'fulfilled'})"), 'warn');
  assert.equal(run("statusTone({status:'partially_refunded', fulfillmentStatus:'fulfilled'})"), 'bad');
  assert.equal(run("statusTone({status:'paid', fulfillmentStatus:'cancelled'})"), 'bad');
  assert.equal(run("statusTone({status:'paid', fulfillmentStatus:'fulfilled'})"), 'good');
});

test('unmerged focus cards still filter by their regular source', () => {
  const { run, node } = appContext();
  node('order-source-filter').options = [{value:'LINE'}];
  run(`
    state.payload = {currency:'JPY',channels:[{channel:'LINE',orders:1,orderDetails:[]}],focusChannels:[]};
    renderOrders = () => {}; focusPanel = () => {}; resetTablePage = () => {};
    openChannelDialog(findChannelRow('LINE', true), 'JPY', true);
    filterChannelOrders();
  `);
  assert.equal(run('state.orderSource'), 'LINE');
});

test('background refresh paints a snapshot once, polls, then paints new data', async () => {
  const { run } = appContext();
  await run(`
    state.followToday = false; state.date='2026-09-07'; state.range='1d';
    let renders = 0; let requests = [];
    const saved = {range:{end:'2026-09-07'},source:{syncedAt:'2026-09-07T09:00:00Z'},kpis:{},refresh:{running:true}};
    const fresh = {...saved,source:{syncedAt:'2026-09-07T10:00:00Z'},refresh:{running:false}};
    const responses = [saved, saved, fresh];
    setBusy = (busy) => { state.busy=busy; }; render = (data) => { state.payload=data; renders++; };
    fetchJson = async (url, options) => { requests.push({url, method: options.method}); return responses.shift(); };
    waitForDashboardPoll = async () => {};
    loadDashboard({force:true});
  `);
  assert.equal(run('renders'), 2);
  assert.equal(run('state.busy'), false);
  assert.equal(run('requests[0].method'), 'POST');
  assert.equal(run('requests.slice(1).every(request => request.url.includes("background=1") && !request.method)'), true);
});

test('a cancelled poll rejects promptly as AbortError', async () => {
  const { run } = appContext();
  const promise = run(`
    const controller = new AbortController();
    const waiting = waitForDashboardPoll(controller.signal, 5000);
    controller.abort();
    waiting;
  `);
  await assert.rejects(promise, {name:'AbortError'});
});

test('cold loading does not render empty KPI data and failure clears busy state', async () => {
  const { run, node } = appContext();
  await run(`
    state.followToday=false; state.range='1d'; state.date='2026-09-07';
    let renders=0;
    render=() => {renders++;}; setBusy=(busy) => {state.busy=busy;};
    fetchJson=async () => ({pending:true,refresh:{running:false,error:'GA4 unavailable'}});
    loadDashboard();
  `);
  assert.equal(run('renders'), 0);
  assert.equal(run('state.busy'), false);
  assert.equal(node('error-panel').textContent, 'GA4 unavailable');
});
