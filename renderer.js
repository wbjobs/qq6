const { ipcRenderer } = require('electron');

let chart;
let backendUrl = 'http://localhost:8000';
let refreshInterval = null;
let currentMetric = 'pm25';
let currentResolution = 40;
let sensorData = {};
let latestInterpolation = null;
let alerts = [];

const ROOM_WIDTH = 20;
const ROOM_HEIGHT = 20;

const metricConfig = {
  pm25: { name: 'PM2.5', unit: 'μg/m³', min: 0, max: 150, color: '#ff6b6b' },
  co2: { name: 'CO₂', unit: 'ppm', min: 400, max: 2000, color: '#ffa502' },
  temperature: { name: '温度', unit: '°C', min: 15, max: 35, color: '#ff4757' },
  humidity: { name: '湿度', unit: '%', min: 20, max: 80, color: '#1e90ff' }
};

const thresholds = {
  pm25: { warn: 75, danger: 150 },
  co2: { warn: 1000, danger: 1500 },
  temperature: { warn: 28, danger: 32 },
  humidity: { warn: 60, danger: 70 }
};

function getValueClass(metric, value) {
  const t = thresholds[metric];
  if (value >= t.danger) return 'danger';
  if (value >= t.warn) return 'warn';
  return '';
}

async function init() {
  try {
    backendUrl = await ipcRenderer.invoke('get-backend-url');
  } catch (e) {
    console.log('使用默认后端URL');
  }

  initChart();
  bindEvents();
  startAutoRefresh();
  updateTime();
  setInterval(updateTime, 1000);
  checkBackendStatus();
  setInterval(checkBackendStatus, 5000);
}

function updateTime() {
  const now = new Date();
  document.getElementById('timeDisplay').textContent = 
    now.toLocaleString('zh-CN', { hour12: false });
}

async function checkBackendStatus() {
  const dot = document.getElementById('backendStatus');
  const text = document.getElementById('backendStatusText');
  try {
    const res = await fetch(`${backendUrl}/health`, { method: 'GET' });
    if (res.ok) {
      dot.classList.add('connected');
      text.textContent = '后端已连接';
    } else {
      throw new Error('bad status');
    }
  } catch (e) {
    dot.classList.remove('connected');
    text.textContent = '后端未连接';
  }
}

function initChart() {
  const chartDom = document.getElementById('mainChart');
  chart = echarts.init(chartDom, null, { renderer: 'canvas' });

  window.addEventListener('resize', () => {
    chart.resize();
  });

  chart.on('click', async (params) => {
    if (params.componentType === 'series' && params.seriesType === 'heatmap') {
      const x = params.value[0];
      const y = params.value[1];
      await showPointValue(x, y);
    }
  });

  updateChartConfig();
}

function bindEvents() {
  document.getElementById('metricSelect').addEventListener('change', (e) => {
    currentMetric = e.target.value;
    updateChartConfig();
    if (latestInterpolation) {
      renderHeatmap(latestInterpolation);
    }
    updateLegend();
    refreshData();
  });

  document.getElementById('resolutionSelect').addEventListener('change', (e) => {
    currentResolution = parseInt(e.target.value);
    refreshData();
  });

  document.getElementById('refreshSelect').addEventListener('change', (e) => {
    clearInterval(refreshInterval);
    startAutoRefresh();
  });

  document.getElementById('refreshBtn').addEventListener('click', refreshData);

  document.getElementById('startSimBtn').addEventListener('click', async () => {
    try {
      const res = await fetch(`${backendUrl}/simulator/start`, { method: 'POST' });
      if (res.ok) {
        updateSimulatorStatus(true);
      }
    } catch (e) {
      alert('启动模拟器失败');
    }
  });

  document.getElementById('stopSimBtn').addEventListener('click', async () => {
    try {
      const res = await fetch(`${backendUrl}/simulator/stop`, { method: 'POST' });
      if (res.ok) {
        updateSimulatorStatus(false);
      }
    } catch (e) {
      alert('停止模拟器失败');
    }
  });

  document.getElementById('clearBtn').addEventListener('click', async () => {
    try {
      await fetch(`${backendUrl}/sensors/clear`, { method: 'POST' });
      sensorData = {};
      latestInterpolation = null;
      alerts = [];
      updateSensorList();
      updateAlerts();
      chart.clear();
      updateChartConfig();
    } catch (e) {
      console.error('清除失败', e);
    }
  });
}

function updateSimulatorStatus(running) {
  const dot = document.getElementById('simulatorStatus');
  const text = document.getElementById('simulatorStatusText');
  if (running) {
    dot.classList.add('connected');
    text.textContent = '模拟器运行中';
  } else {
    dot.classList.remove('connected');
    text.textContent = '模拟器未启动';
  }
}

function startAutoRefresh() {
  const interval = parseInt(document.getElementById('refreshSelect').value);
  refreshInterval = setInterval(refreshData, interval);
}

async function refreshData() {
  try {
    await Promise.all([fetchSensors(), fetchInterpolation(), fetchSimulatorStatus()]);
  } catch (e) {
    console.error('刷新数据失败:', e);
  }
}

async function fetchSensors() {
  try {
    const res = await fetch(`${backendUrl}/sensors`);
    if (res.ok) {
      const data = await res.json();
      sensorData = {};
      data.forEach(s => {
        sensorData[s.id] = s;
      });
      updateSensorList();
      checkAlerts(data);
    }
  } catch (e) {
    console.error('获取传感器数据失败', e);
  }
}

async function fetchInterpolation() {
  try {
    const res = await fetch(
      `${backendUrl}/interpolate?metric=${currentMetric}&resolution=${currentResolution}`
    );
    if (res.ok) {
      const data = await res.json();
      latestInterpolation = data;
      renderHeatmap(data);
    }
  } catch (e) {
    console.error('获取插值数据失败', e);
  }
}

async function fetchSimulatorStatus() {
  try {
    const res = await fetch(`${backendUrl}/simulator/status`);
    if (res.ok) {
      const data = await res.json();
      updateSimulatorStatus(data.running);
    }
  } catch (e) {}
}

async function showPointValue(x, y) {
  try {
    const res = await fetch(
      `${backendUrl}/point?x=${x.toFixed(2)}&y=${y.toFixed(2)}`
    );
    if (res.ok) {
      const data = await res.json();
      const display = document.getElementById('clickValueDisplay');
      display.innerHTML = `
        <div class="value-item">
          <span class="value-label">位置坐标</span>
          <span class="value-num" style="font-size:14px;">(${x.toFixed(1)}m, ${y.toFixed(1)}m)</span>
        </div>
        <div class="value-item">
          <span class="value-label">PM2.5</span>
          <span class="value-num" style="color:${getValueColor('pm25', data.pm25)}">${data.pm25.toFixed(1)}<span class="value-unit">μg/m³</span></span>
        </div>
        <div class="value-item">
          <span class="value-label">CO₂</span>
          <span class="value-num" style="color:${getValueColor('co2', data.co2)}">${data.co2.toFixed(0)}<span class="value-unit">ppm</span></span>
        </div>
        <div class="value-item">
          <span class="value-label">温度</span>
          <span class="value-num" style="color:${getValueColor('temperature', data.temperature)}">${data.temperature.toFixed(1)}<span class="value-unit">°C</span></span>
        </div>
        <div class="value-item">
          <span class="value-label">湿度</span>
          <span class="value-num" style="color:${getValueColor('humidity', data.humidity)}">${data.humidity.toFixed(1)}<span class="value-unit">%</span></span>
        </div>
      `;
    }
  } catch (e) {
    console.error('获取点数据失败', e);
  }
}

function getValueColor(metric, value) {
  const t = thresholds[metric];
  if (value >= t.danger) return '#f44336';
  if (value >= t.warn) return '#ffc107';
  return '#fff';
}

function checkAlerts(sensors) {
  const newAlerts = [];
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  
  sensors.forEach(s => {
    Object.keys(thresholds).forEach(metric => {
      const val = s[metric];
      const t = thresholds[metric];
      const cfg = metricConfig[metric];
      if (val >= t.danger) {
        newAlerts.push({
          type: 'danger',
          text: `${s.name} ${cfg.name}严重超标: ${val.toFixed(1)}${cfg.unit}`,
          time: now
        });
      } else if (val >= t.warn) {
        newAlerts.push({
          type: 'warning',
          text: `${s.name} ${cfg.name}警告: ${val.toFixed(1)}${cfg.unit}`,
          time: now
        });
      }
    });
  });

  if (newAlerts.length > 0 || alerts.length > 0) {
    alerts = [...newAlerts, ...alerts].slice(0, 20);
    updateAlerts();
  }
}

function updateAlerts() {
  const panel = document.getElementById('alertPanel');
  if (alerts.length === 0) {
    panel.innerHTML = `<div style="color:rgba(255,255,255,0.5);text-align:center;padding:10px;font-size:12px;">暂无告警</div>`;
    return;
  }

  panel.innerHTML = alerts.map(a => `
    <div class="alert-item ${a.type === 'warning' ? 'warning' : ''}">
      ${a.text}
      <div class="alert-time">${a.time}</div>
    </div>
  `).join('');
}

function updateSensorList() {
  const list = document.getElementById('sensorList');
  const sensors = Object.values(sensorData);

  if (sensors.length === 0) {
    list.innerHTML = `<div style="color:rgba(255,255,255,0.5);text-align:center;padding:20px;">暂无传感器数据，请启动模拟器</div>`;
    return;
  }

  list.innerHTML = sensors.map(s => `
    <div class="sensor-item" onclick="focusSensor('${s.id}')">
      <div class="sensor-header">
        <span class="sensor-name">📍 ${s.name}</span>
        <span class="sensor-coord">(${s.x}m, ${s.y}m)</span>
      </div>
      <div class="sensor-readings">
        <div class="reading">PM2.5: <span class="reading-value ${getValueClass('pm25', s.pm25)}">${s.pm25.toFixed(1)}</span></div>
        <div class="reading">CO₂: <span class="reading-value ${getValueClass('co2', s.co2)}">${s.co2.toFixed(0)}</span></div>
        <div class="reading">温度: <span class="reading-value ${getValueClass('temperature', s.temperature)}">${s.temperature.toFixed(1)}</span></div>
        <div class="reading">湿度: <span class="reading-value ${getValueClass('humidity', s.humidity)}">${s.humidity.toFixed(1)}</span></div>
      </div>
    </div>
  `).join('');
}

window.focusSensor = function(id) {
  const s = sensorData[id];
  if (s && chart) {
    chart.dispatchAction({
      type: 'showTip',
      seriesIndex: 1,
      dataIndex: Object.keys(sensorData).indexOf(id)
    });
  }
};

function renderHeatmap(data) {
  const cfg = metricConfig[currentMetric];
  const { grid, min_val, max_val, sensors } = data;

  const heatmapData = [];
  const n = grid.length;
  const stepX = ROOM_WIDTH / (n - 1);
  const stepY = ROOM_HEIGHT / (n - 1);

  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      heatmapData.push([
        +(j * stepX).toFixed(2),
        +(i * stepY).toFixed(2),
        +grid[i][j].toFixed(2)
      ]);
    }
  }

  const sensorPoints = sensors.map(s => ({
    value: [s.x, s.y, s[currentMetric]],
    name: s.name
  }));

  const option = {
    tooltip: {
      trigger: 'item',
      formatter: function(params) {
        if (params.seriesName === '传感器') {
          const s = sensorData[params.data.name];
          if (s) {
            return `<div style="padding:8px;">
              <b>${s.name}</b><br/>
              位置: (${s.x}m, ${s.y}m)<br/>
              PM2.5: ${s.pm25.toFixed(1)} μg/m³<br/>
              CO₂: ${s.co2.toFixed(0)} ppm<br/>
              温度: ${s.temperature.toFixed(1)} °C<br/>
              湿度: ${s.humidity.toFixed(1)} %
            </div>`;
          }
        }
        return `${cfg.name}: ${params.value[2].toFixed(2)} ${cfg.unit}<br/>
                坐标: (${params.value[0].toFixed(1)}m, ${params.value[1].toFixed(1)}m)`;
      }
    },
    xAxis: {
      type: 'value',
      min: 0,
      max: ROOM_WIDTH,
      name: 'X (m)',
      nameTextStyle: { color: 'rgba(255,255,255,0.7)' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } },
      axisLabel: { color: 'rgba(255,255,255,0.6)' },
      splitLine: { show: false }
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: ROOM_HEIGHT,
      name: 'Y (m)',
      nameTextStyle: { color: 'rgba(255,255,255,0.7)' },
      axisLine: { lineStyle: { color: 'rgba(255,255,255,0.3)' } },
      axisLabel: { color: 'rgba(255,255,255,0.6)' },
      splitLine: { show: false }
    },
    grid: {
      left: '8%',
      right: '12%',
      top: '8%',
      bottom: '12%'
    },
    visualMap: {
      min: Math.floor(min_val),
      max: Math.ceil(max_val),
      calculable: true,
      orient: 'vertical',
      right: 10,
      top: 'center',
      inRange: {
        color: getColorRange(currentMetric)
      },
      textStyle: { color: 'rgba(255,255,255,0.7)' },
      text: [cfg.unit, '']
    },
    graphic: [
      {
        type: 'group',
        left: 'center',
        bottom: 10,
        children: [
          {
            type: 'text',
            style: {
              text: '💡 点击热力图任意位置查看详细数值',
              fill: 'rgba(255,255,255,0.5)',
              fontSize: 12
            }
          }
        ]
      }
    ],
    series: [
      {
        name: cfg.name,
        type: 'heatmap',
        data: heatmapData,
        itemStyle: {
          borderWidth: 0
        },
        emphasis: {
          itemStyle: {
            borderColor: '#fff',
            borderWidth: 1
          }
        },
        progressive: 1000,
        animation: false
      },
      {
        name: '传感器',
        type: 'scatter',
        data: sensorPoints,
        symbolSize: 14,
        symbol: 'pin',
        itemStyle: {
          color: '#fff',
          borderColor: '#667eea',
          borderWidth: 2,
          shadowBlur: 10,
          shadowColor: 'rgba(102, 126, 234, 0.6)'
        },
        label: {
          show: true,
          formatter: function(p) { return p.data.name; },
          position: 'top',
          color: '#fff',
          fontSize: 10,
          backgroundColor: 'rgba(0,0,0,0.5)',
          padding: [2, 5],
          borderRadius: 3
        },
        zlevel: 10
      },
      {
        name: '房间布局',
        type: 'custom',
        renderItem: function(params, api) {
          return {
            type: 'group',
            children: [
              {
                type: 'rect',
                shape: {
                  x: api.coord([0, 0])[0],
                  y: api.coord([0, ROOM_HEIGHT])[1],
                  width: api.coord([ROOM_WIDTH, 0])[0] - api.coord([0, 0])[0],
                  height: api.coord([0, 0])[1] - api.coord([0, ROOM_HEIGHT])[1]
                },
                style: {
                  fill: 'transparent',
                  stroke: 'rgba(255,255,255,0.6)',
                  lineWidth: 3,
                  lineDash: [0]
                }
              },
              {
                type: 'line',
                shape: {
                  x1: api.coord([8, 0])[0],
                  y1: api.coord([8, 0])[1],
                  x2: api.coord([8, 8])[0],
                  y2: api.coord([8, 8])[1]
                },
                style: {
                  stroke: 'rgba(255,255,255,0.4)',
                  lineWidth: 2,
                  lineDash: [5, 5]
                }
              },
              {
                type: 'line',
                shape: {
                  x1: api.coord([0, 12])[0],
                  y1: api.coord([0, 12])[1],
                  x2: api.coord([12, 12])[0],
                  y2: api.coord([12, 12])[1]
                },
                style: {
                  stroke: 'rgba(255,255,255,0.4)',
                  lineWidth: 2,
                  lineDash: [5, 5]
                }
              },
              {
                type: 'text',
                style: {
                  text: '办公室A',
                  x: api.coord([4, 16])[0],
                  y: api.coord([4, 16])[1],
                  fill: 'rgba(255,255,255,0.4)',
                  fontSize: 12,
                  textAlign: 'center',
                  textVerticalAlign: 'middle'
                }
              },
              {
                type: 'text',
                style: {
                  text: '会议室',
                  x: api.coord([14, 16])[0],
                  y: api.coord([14, 16])[1],
                  fill: 'rgba(255,255,255,0.4)',
                  fontSize: 12,
                  textAlign: 'center',
                  textVerticalAlign: 'middle'
                }
              },
              {
                type: 'text',
                style: {
                  text: '办公室B',
                  x: api.coord([4, 6])[0],
                  y: api.coord([4, 6])[1],
                  fill: 'rgba(255,255,255,0.4)',
                  fontSize: 12,
                  textAlign: 'center',
                  textVerticalAlign: 'middle'
                }
              },
              {
                type: 'text',
                style: {
                  text: '休息区',
                  x: api.coord([14, 6])[0],
                  y: api.coord([14, 6])[1],
                  fill: 'rgba(255,255,255,0.4)',
                  fontSize: 12,
                  textAlign: 'center',
                  textVerticalAlign: 'middle'
                }
              }
            ]
          };
        },
        data: [0],
        zlevel: 5,
        silent: true
      }
    ]
  };

  chart.setOption(option, true);
}

function getColorRange(metric) {
  switch (metric) {
    case 'pm25':
      return ['#006837', '#1a9850', '#66bd63', '#a6d96a', '#d9ef8b', '#fee08b', '#fdae61', '#f46d43', '#d73027', '#a50026'];
    case 'co2':
      return ['#2c7bb6', '#00a6ca', '#00ccbc', '#90eb9d', '#ffff8c', '#f9d057', '#f29e2e', '#e76818', '#d7191c'];
    case 'temperature':
      return ['#313695', '#4575b4', '#74add1', '#abd9e9', '#e0f3f8', '#fee090', '#fdae61', '#f46d43', '#d73027', '#a50026'];
    case 'humidity':
      return ['#d73027', '#f46d43', '#fdae61', '#fee090', '#ffffbf', '#e0f3f8', '#abd9e9', '#74add1', '#4575b4', '#313695'];
    default:
      return ['#313695', '#4575b4', '#74add1', '#abd9e9', '#e0f3f8', '#ffffbf', '#fee090', '#fdae61', '#f46d43', '#d73027'];
  }
}

function updateLegend() {
  const cfg = metricConfig[currentMetric];
  const colors = getColorRange(currentMetric);
  const t = thresholds[currentMetric];
  const range = cfg.max - cfg.min;

  const segments = [
    { label: `优良 (<${t.warn}${cfg.unit})`, color: colors[2] },
    { label: `轻度污染 (${t.warn}-${t.danger}${cfg.unit})`, color: colors[5] },
    { label: `重度污染 (>${t.danger}${cfg.unit})`, color: colors[9] }
  ];

  document.getElementById('legend').innerHTML = segments.map(s => `
    <div class="legend-item">
      <div class="legend-color" style="background:${s.color};"></div>
      <span>${s.label}</span>
    </div>
  `).join('');
}

function updateChartConfig() {
  updateLegend();
  const cfg = metricConfig[currentMetric];
  const option = {
    title: {
      show: true,
      text: `${cfg.name}浓度分布热力图 (克里金插值)`,
      left: 'center',
      top: 10,
      textStyle: {
        color: '#fff',
        fontSize: 16,
        fontWeight: 600
      }
    }
  };
  if (chart) {
    chart.setOption(option);
  }
}

init();
