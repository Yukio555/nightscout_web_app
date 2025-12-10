from flask import Flask, render_template_string, request, jsonify
import requests
from datetime import datetime, timedelta
import hashlib
import pytz
import json

app = Flask(__name__)

# Nightscout設定
NIGHTSCOUT_URL = "https://ren-cgm.azurewebsites.net"
API_SECRET = "enoq19780509yyy"
API_SECRET_HASH = hashlib.sha1(API_SECRET.encode()).hexdigest()
JST = pytz.timezone('Asia/Tokyo')

def get_nightscout_data(date_str):
    """指定日のNightscoutデータを取得"""
    jst_start = JST.localize(datetime.strptime(date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S"))
    jst_end = jst_start + timedelta(days=1)
    
    utc_start = jst_start.astimezone(pytz.UTC)
    utc_end = jst_end.astimezone(pytz.UTC)
    
    headers = {"API-SECRET": API_SECRET_HASH}
    
    # 血糖値データ取得
    entries_url = f"{NIGHTSCOUT_URL}/api/v1/entries.json"
    params = {
        "find[dateString][$gte]": utc_start.isoformat(),
        "find[dateString][$lt]": utc_end.isoformat(),
        "count": 1000
    }
    entries_response = requests.get(entries_url, headers=headers, params=params)
    entries = entries_response.json() if entries_response.status_code == 200 else []
    
    # トリートメント取得
    treatments_url = f"{NIGHTSCOUT_URL}/api/v1/treatments.json"
    treatments_params = {
        "find[created_at][$gte]": utc_start.isoformat(),
        "find[created_at][$lt]": utc_end.isoformat(),
        "count": 1000
    }
    treatments_response = requests.get(treatments_url, headers=headers, params=treatments_params)
    treatments = treatments_response.json() if treatments_response.status_code == 200 else []
    
    return entries, treatments

def get_direction_arrow(direction):
    """トレンド方向を矢印に変換"""
    arrows = {
        'DoubleUp': '⇈', 'SingleUp': '↑', 'FortyFiveUp': '↗',
        'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓',
        'DoubleDown': '⇊', 'NOT COMPUTABLE': '?', 'RATE OUT OF RANGE': '?'
    }
    return arrows.get(direction, '')

def parse_notes(notes):
    """ノート欄からデータを抽出"""
    if not notes:
        return None, None, None, [], None
    
    lines = notes.strip().split('\n')
    cir = None
    predicted_insulin = None
    insulin_type = None
    foods = []
    basal_amount = None
    
    if len(lines) > 0:
        first_line = lines[0].strip()
        
        if first_line.startswith('Tore ') or first_line.startswith('トレ '):
            foods.append('基礎インスリン')
            parts = first_line.split()
            if len(parts) >= 2:
                try:
                    basal_amount = float(parts[1])
                except:
                    pass
            for i in range(1, len(lines)):
                if lines[i].strip():
                    foods.append(lines[i].strip())
            return cir, predicted_insulin, insulin_type, foods, basal_amount
        
        if first_line.upper() == 'B':
            foods.append('ぶどう糖補食')
            for i in range(1, len(lines)):
                if lines[i].strip():
                    foods.append(lines[i].strip())
            return cir, predicted_insulin, insulin_type, foods, basal_amount
        
        if first_line.upper() in ['N', 'F']:
            insulin_type = first_line.upper()
            for i in range(1, len(lines)):
                if lines[i].strip():
                    foods.append(lines[i].strip())
            return cir, predicted_insulin, insulin_type, foods, basal_amount
        
        test_line = first_line.replace('cir', '').replace('CIR', '').replace('Cir', '').strip()
        parts = test_line.split()
        is_cir_format = False
        
        if len(parts) >= 1:
            try:
                float(parts[0])
                is_cir_format = True
            except:
                pass
        
        if is_cir_format:
            if len(parts) >= 1:
                try:
                    cir = float(parts[0])
                except:
                    pass
            if len(parts) >= 2:
                second_part = parts[1].strip()
                if second_part and second_part[-1].upper() in ['N', 'F']:
                    insulin_type = second_part[-1].upper()
                    insulin_part = second_part[:-1]
                else:
                    insulin_part = second_part
                try:
                    predicted_insulin = float(insulin_part)
                except:
                    pass
            for i in range(1, len(lines)):
                if lines[i].strip():
                    foods.append(lines[i].strip())
        else:
            for line in lines:
                if line.strip():
                    foods.append(line.strip())
    
    return cir, predicted_insulin, insulin_type, foods, basal_amount

def prepare_report_data(date_str, entries, treatments):
    """レポート用データを準備"""
    # グラフ用データ
    entries_sorted = sorted(entries, key=lambda x: x['dateString'])
    chart_times = []
    chart_bgs = []
    
    for e in entries_sorted:
        try:
            time = datetime.fromisoformat(e['dateString'].replace('Z', '+00:00'))
            time_jst = time.astimezone(JST)
            bg = e.get('sgv')
            if bg is not None:
                chart_times.append(time_jst.strftime('%H:%M'))
                chart_bgs.append(bg)
        except:
            continue
    
    # 統計情報の計算
    total_insulin = 0
    basal_insulin = 0
    total_carbs = 0
    bg_values_for_avg = [e.get('sgv') for e in entries if e.get('sgv') is not None]
    avg_bg = round(sum(bg_values_for_avg) / len(bg_values_for_avg)) if bg_values_for_avg else 0
    
    # トリートメントを時系列順にソート
    treatments_sorted = sorted(treatments, key=lambda x: x.get('created_at', ''))
    
    # テーブル行データ
    table_data = []
    for treatment in treatments_sorted:
        time_utc = datetime.fromisoformat(treatment.get('created_at', '').replace('Z', '+00:00'))
        time_jst = time_utc.astimezone(JST)
        time_str = time_jst.strftime('%H:%M')
        
        # 実測値とCGM血糖値を両方取得
        bg_check_value = treatment.get('glucose')
        bg_display = ""
        
        # CGM血糖値を取得
        if entries:
            try:
                closest_entry = min(entries, 
                                  key=lambda x: abs((datetime.fromisoformat(x['dateString'].replace('Z', '+00:00')).astimezone(JST) - time_jst).total_seconds()))
                cgm_value = closest_entry.get('sgv')
                delta = closest_entry.get('delta')
                direction = closest_entry.get('direction')
                
                if cgm_value:
                    bg_display = str(cgm_value)
                    if delta is not None:
                        delta_rounded = round(delta)
                        bg_display += f" ({'+' if delta_rounded > 0 else ''}{delta_rounded})"
                    if direction:
                        bg_display += f" {get_direction_arrow(direction)}"
            except:
                pass
        
        # 実測値がある場合は追加表示
        if bg_check_value:
            if bg_display:
                bg_display += f" / 実測:{bg_check_value}"
            else:
                bg_display = f"実測:{bg_check_value}"
        
        # ノートからデータを抽出
        notes = treatment.get('notes', '')
        cir, predicted_insulin, insulin_type, foods, basal_amount = parse_notes(notes)
        
        carbs = treatment.get('carbs', '')
        actual_insulin = treatment.get('insulin', '')
        
        # 糖質量が1～3gの場合は自動的に補食とする
        if carbs:
            try:
                carbs_value = float(carbs)
                if 1 <= carbs_value <= 3:
                    # まだ補食が追加されていない場合のみ追加
                    if 'ぶどう糖補食' not in foods and '補食' not in ''.join(foods):
                        foods.insert(0, '補食')
            except:
                pass
        
        # 統計の計算
        is_basal = any(food == '基礎インスリン' for food in foods)
        if is_basal and basal_amount:
            basal_insulin += basal_amount
        elif actual_insulin:
            try:
                total_insulin += float(actual_insulin)
            except:
                pass
        
        if carbs:
            try:
                total_carbs += float(carbs)
            except:
                pass
        
        # 食べ物を結合
        food_text = ", ".join(foods) if foods else "-"
        
        # bg_displayが空の場合は"-"を設定
        if not bg_display:
            bg_display = "-"
        
        table_data.append({
            'time': time_str,
            'bg': bg_display,
            'cir': cir if cir else '-',
            'carbs': f"{carbs}g" if carbs else '-',
            'predicted': predicted_insulin if predicted_insulin else '-',
            'actual': actual_insulin if actual_insulin else '-',
            'type': insulin_type if insulin_type else '-',
            'food': food_text
        })
    
    # TCIR計算
    tcir = f"{total_carbs / total_insulin:.1f}" if total_insulin > 0 else "-"
    
    return {
        'chart_times': chart_times,
        'chart_bgs': chart_bgs,
        'avg_bg': avg_bg,
        'total_insulin': total_insulin,
        'basal_insulin': basal_insulin,
        'total_carbs': total_carbs,
        'tcir': tcir,
        'table_data': table_data
    }

# HTMLテンプレート
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nightscout日次レポート</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/3.9.1/chart.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Arial', 'Helvetica', 'Meiryo', sans-serif;
            background-color: #f5f5f5;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px 20px;
            text-align: center;
            box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        }
        .header h1 {
            font-size: 28px;
            margin-bottom: 15px;
        }
        .date-selector {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 10px;
            margin-top: 15px;
        }
        .date-selector input {
            padding: 10px 15px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
        }
        .date-selector button {
            padding: 10px 20px;
            background-color: white;
            color: #667eea;
            border: none;
            border-radius: 5px;
            font-weight: bold;
            cursor: pointer;
            transition: all 0.3s;
        }
        .date-selector button:hover {
            background-color: #f0f0f0;
            transform: translateY(-2px);
        }
        .loading {
            text-align: center;
            padding: 50px;
            font-size: 18px;
            color: #666;
            display: none;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            min-height: calc(100vh - 180px);
        }
        .graph-section {
            position: sticky;
            top: 0;
            z-index: 100;
            padding: 20px;
            background-color: white;
            border-bottom: 2px solid #ddd;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        .chart-wrapper {
            height: 200px;
            margin-bottom: 10px;
        }
        .table-section {
            padding: 0 20px 20px 20px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }
        thead {
            position: sticky;
            top: 242px;
            z-index: 50;
        }
        th {
            background-color: #667eea;
            color: white;
            padding: 12px 8px;
            text-align: left;
            font-weight: bold;
            border: 1px solid #ddd;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        td {
            padding: 10px 8px;
            border: 1px solid #ddd;
            background-color: white;
        }
        tr:nth-child(even) td {
            background-color: #f9f9f9;
        }
        tr:hover td {
            background-color: #fffacd;
            cursor: pointer;
        }
        .stats-section {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 15px;
            padding: 20px;
            background-color: #f8f9fa;
        }
        .stat-box {
            background-color: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            text-align: center;
        }
        .stat-label {
            font-size: 12px;
            color: #666;
            margin-bottom: 8px;
            font-weight: bold;
        }
        .stat-value {
            font-size: 28px;
            color: #333;
            font-weight: bold;
        }
        .stat-unit {
            font-size: 16px;
            color: #888;
            margin-left: 3px;
        }
        .footer {
            text-align: center;
            color: #666;
            font-size: 12px;
            padding: 20px;
        }
        @media print {
            .header { position: relative; }
            .graph-section { position: relative; top: auto; box-shadow: none; }
            thead { position: relative; top: auto; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Nightscout日次レポート</h1>
        <div class="date-selector">
            <input type="date" id="dateInput" value="{{ today }}">
            <button onclick="loadReport()">レポート表示</button>
        </div>
    </div>
    
    <div class="loading" id="loading">データを読み込んでいます...</div>
    
    <div class="container" id="reportContainer" style="display: none;">
        <div class="graph-section">
            <div class="chart-wrapper">
                <canvas id="bgChart"></canvas>
            </div>
        </div>
        
        <div class="table-section">
            <table id="dataTable">
                <thead>
                    <tr>
                        <th>時刻</th>
                        <th>血糖値</th>
                        <th>CIR</th>
                        <th>糖質</th>
                        <th>予想</th>
                        <th>打った</th>
                        <th>種類</th>
                        <th>食べたもの</th>
                    </tr>
                </thead>
                <tbody id="tableBody">
                </tbody>
            </table>
        </div>
        
        <div class="stats-section">
            <div class="stat-box">
                <div class="stat-label">平均血糖値</div>
                <div class="stat-value"><span id="avgBg">-</span><span class="stat-unit">mg/dL</span></div>
            </div>
            <div class="stat-box">
                <div class="stat-label">インスリン総量</div>
                <div class="stat-value"><span id="totalInsulin">-</span><span class="stat-unit">単位</span></div>
                <div style="font-size: 11px; color: #999; margin-top: 5px;">(基礎除く)</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">基礎インスリン</div>
                <div class="stat-value"><span id="basalInsulin">-</span><span class="stat-unit">単位</span></div>
            </div>
            <div class="stat-box">
                <div class="stat-label">糖質総量</div>
                <div class="stat-value"><span id="totalCarbs">-</span><span class="stat-unit">g</span></div>
            </div>
            <div class="stat-box">
                <div class="stat-label">TCIR</div>
                <div class="stat-value" id="tcir">-</div>
            </div>
        </div>
        
        <div class="footer">
            <p>Generated from Nightscout: {{ nightscout_url }}</p>
        </div>
    </div>
    
    <script>
        let bgChart = null;
        
        async function loadReport() {
            const dateInput = document.getElementById('dateInput');
            const date = dateInput.value;
            
            document.getElementById('loading').style.display = 'block';
            document.getElementById('reportContainer').style.display = 'none';
            
            try {
                const response = await fetch(`/api/report?date=${date}`);
                const data = await response.json();
                
                if (data.error) {
                    alert(data.error);
                    return;
                }
                
                // グラフ描画
                drawChart(data);
                
                // テーブル描画
                drawTable(data.table_data);
                
                // 統計表示
                document.getElementById('avgBg').textContent = data.avg_bg;
                document.getElementById('totalInsulin').textContent = data.total_insulin;
                document.getElementById('basalInsulin').textContent = data.basal_insulin;
                document.getElementById('totalCarbs').textContent = data.total_carbs;
                document.getElementById('tcir').textContent = data.tcir;
                
                document.getElementById('loading').style.display = 'none';
                document.getElementById('reportContainer').style.display = 'block';
                
                // テーブル描画後にホバー機能をセットアップ
                setTimeout(() => {
                    setupTableHover(data);
                }, 100);
            } catch (error) {
                alert('データの取得に失敗しました: ' + error);
                document.getElementById('loading').style.display = 'none';
            }
        }
        
        function drawChart(data) {
            const ctx = document.getElementById('bgChart').getContext('2d');
            
            if (bgChart) {
                bgChart.destroy();
            }
            
            bgChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.chart_times,
                    datasets: [{
                        label: '血糖値',
                        data: data.chart_bgs,
                        borderColor: '#667eea',
                        backgroundColor: 'rgba(102, 126, 234, 0.1)',
                        borderWidth: 2,
                        tension: 0.1,
                        pointRadius: 3,
                        pointHoverRadius: 6
                    },
                    {
                        label: '目標範囲下限',
                        data: Array(data.chart_times.length).fill(70),
                        borderColor: '#4CAF50',
                        borderDash: [5, 5],
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: false
                    },
                    {
                        label: '目標範囲上限',
                        data: Array(data.chart_times.length).fill(180),
                        borderColor: '#4CAF50',
                        borderDash: [5, 5],
                        borderWidth: 2,
                        pointRadius: 0,
                        fill: '-1',
                        backgroundColor: 'rgba(76, 175, 80, 0.1)'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        title: {
                            display: true,
                            text: '血糖値推移',
                            font: { size: 16, weight: 'bold' }
                        },
                        legend: { display: false },
                        tooltip: { mode: 'index', intersect: false }
                    },
                    scales: {
                        y: {
                            beginAtZero: false,
                            min: 0,
                            max: 400,
                            title: {
                                display: true,
                                text: '血糖値 (mg/dL)',
                                font: { weight: 'bold' }
                            }
                        },
                        x: {
                            ticks: {
                                maxRotation: 0,
                                minRotation: 0,
                                autoSkip: false,
                                font: {
                                    size: 12
                                },
                                callback: function(value, index, values) {
                                    const label = this.getLabelForValue(value);
                                    if (!label) return '';
                                    
                                    // HH:MM形式から時間部分を抽出
                                    const timeParts = label.split(':');
                                    const hour = parseInt(timeParts[0]);
                                    const minute = parseInt(timeParts[1]);
                                    
                                    // 最初のラベルは常に表示
                                    if (index === 0) {
                                        return hour + '時';
                                    }
                                    
                                    // 1時間おきに表示（00分、05分、10分など最も近いもの）
                                    // 前のラベルの時間と比較
                                    if (index > 0) {
                                        const prevLabel = this.getLabelForValue(index - 1);
                                        if (prevLabel) {
                                            const prevHour = parseInt(prevLabel.split(':')[0]);
                                            // 時間が変わったら表示
                                            if (hour !== prevHour) {
                                                return hour + '時';
                                            }
                                        }
                                    }
                                    
                                    return '';
                                }
                            },
                            title: {
                                display: true,
                                text: '時刻',
                                font: { weight: 'bold' }
                            }
                        }
                    }
                }
            });
            
            // グラフ描画後にホバー機能をセットアップ（drawChartの最後から削除）
        }
        
        function drawTable(tableData) {
            const tbody = document.getElementById('tableBody');
            tbody.innerHTML = '';
            
            tableData.forEach(row => {
                const tr = document.createElement('tr');
                tr.dataset.time = row.time;
                tr.innerHTML = `
                    <td>${row.time}</td>
                    <td>${row.bg}</td>
                    <td>${row.cir}</td>
                    <td>${row.carbs}</td>
                    <td>${row.predicted}</td>
                    <td>${row.actual}</td>
                    <td>${row.type}</td>
                    <td>${row.food}</td>
                `;
                tbody.appendChild(tr);
            });
        }
        
        function setupTableHover(data) {
            console.log('Setting up table hover with data:', data);
            const tableRows = document.querySelectorAll('#dataTable tbody tr');
            console.log('Found table rows:', tableRows.length);
            let highlightDataset = null;
            
            tableRows.forEach((row, rowIndex) => {
                row.addEventListener('mouseenter', function() {
                    console.log('Mouse entered row:', rowIndex);
                    const time = this.dataset.time;
                    console.log('Row time:', time);
                    
                    if (!time || !bgChart) {
                        console.log('Missing time or chart');
                        return;
                    }
                    
                    let index = data.chart_times.indexOf(time);
                    console.log('Direct match index:', index);
                    
                    if (index === -1) {
                        const targetMinutes = parseInt(time.split(':')[0]) * 60 + parseInt(time.split(':')[1]);
                        let minDiff = Infinity;
                        
                        data.chart_times.forEach((label, i) => {
                            const labelMinutes = parseInt(label.split(':')[0]) * 60 + parseInt(label.split(':')[1]);
                            const diff = Math.abs(targetMinutes - labelMinutes);
                            if (diff < minDiff) {
                                minDiff = diff;
                                index = i;
                            }
                        });
                        console.log('Closest match index:', index);
                    }
                    
                    if (index === -1 || index >= data.chart_bgs.length || !data.chart_bgs[index]) {
                        console.log('Invalid index or no data at index');
                        return;
                    }
                    
                    console.log('Highlighting index:', index, 'value:', data.chart_bgs[index]);
                    
                    // 既存のハイライトを削除
                    bgChart.data.datasets = bgChart.data.datasets.filter(ds => ds.label !== 'ハイライト' && ds.label !== 'ハイライト線');
                    
                    // ポイントのハイライト（大きな星マーク）
                    const highlightData = Array(data.chart_times.length).fill(null);
                    highlightData[index] = data.chart_bgs[index];
                    
                    highlightDataset = {
                        label: 'ハイライト',
                        data: highlightData,
                        borderColor: '#FF6B35',
                        backgroundColor: '#FF6B35',
                        pointRadius: 15,
                        pointHoverRadius: 18,
                        showLine: false,
                        pointStyle: 'star',
                        borderWidth: 3
                    };
                    
                    // 垂直線を追加（注目ポイントを強調）
                    const lineData = Array(data.chart_times.length).fill(null);
                    lineData[index] = data.chart_bgs[index];
                    
                    const verticalLine = {
                        label: 'ハイライト線',
                        data: lineData,
                        borderColor: 'rgba(255, 107, 53, 0.3)',
                        backgroundColor: 'rgba(255, 107, 53, 0.2)',
                        pointRadius: 0,
                        showLine: true,
                        borderWidth: 4,
                        borderDash: [10, 5],
                        fill: false
                    };
                    
                    bgChart.data.datasets.push(highlightDataset);
                    bgChart.data.datasets.push(verticalLine);
                    bgChart.update('none');
                });
                
                row.addEventListener('mouseleave', function() {
                    console.log('Mouse left row');
                    if (bgChart) {
                        bgChart.data.datasets = bgChart.data.datasets.filter(ds => ds.label !== 'ハイライト' && ds.label !== 'ハイライト線');
                        highlightDataset = null;
                        bgChart.update('none');
                    }
                });
            });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    """メインページ"""
    today = datetime.now().strftime("%Y-%m-%d")
    return render_template_string(HTML_TEMPLATE, today=today, nightscout_url=NIGHTSCOUT_URL)

@app.route('/api/report')
def get_report():
    """レポートデータをJSON形式で返す"""
    date_str = request.args.get('date', datetime.now().strftime("%Y-%m-%d"))
    
    try:
        entries, treatments = get_nightscout_data(date_str)
        report_data = prepare_report_data(date_str, entries, treatments)
        return jsonify(report_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    print("=" * 60)
    print("Nightscout日次レポートアプリを起動しています...")
    print("ブラウザで以下のURLを開いてください:")
    print("http://localhost:5000")
    print("=" * 60)
    print("終了するには Ctrl+C を押してください")
    print("=" * 60)
    app.run(debug=True, host='0.0.0.0', port=5000)
