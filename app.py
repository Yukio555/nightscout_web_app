import os
import logging
import hashlib
import pytz
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify
from typing import Tuple, List, Dict, Any, Optional

# --- 設定とロギングの初期化 ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 環境変数から設定を読み込む（デフォルト値付き）
# 重要: 本番環境では必ず環境変数で設定すること
NIGHTSCOUT_URL = os.environ.get("NIGHTSCOUT_URL", "https://ren-cgm.azurewebsites.net")
API_SECRET = os.environ.get("API_SECRET")

# タイムゾーン定義
JST = pytz.timezone('Asia/Tokyo')

class NightscoutClient:
    """Nightscoutとの通信とデータ処理を担当するクラス"""
    
    def __init__(self, url: str, secret: str):
        if not secret:
            logger.warning("API_SECRETが設定されていません。データ取得に失敗する可能性があります。")
            self.headers = {}
        else:
            secret_hash = hashlib.sha1(secret.encode()).hexdigest()
            self.headers = {"API-SECRET": secret_hash}
        self.url = url.rstrip('/')

    def fetch_data(self, date_str: str) -> Tuple[List[Dict], List[Dict]]:
        """指定日のEntries(血糖値)とTreatments(処置)を取得"""
        try:
            # 日付範囲の計算
            jst_start = JST.localize(datetime.strptime(f"{date_str} 00:00:00", "%Y-%m-%d %H:%M:%S"))
            jst_end = jst_start + timedelta(days=1)
            
            utc_start = jst_start.astimezone(pytz.UTC)
            utc_end = jst_end.astimezone(pytz.UTC)
            
            common_params = {
                "count": 2000  # データ量が多い場合に備えて少し増やす
            }

            # 1. 血糖値データ (Entries)
            entries_params = {
                **common_params,
                "find[dateString][$gte]": utc_start.isoformat(),
                "find[dateString][$lt]": utc_end.isoformat(),
            }
            res_entries = requests.get(f"{self.url}/api/v1/entries.json", headers=self.headers, params=entries_params, timeout=10)
            res_entries.raise_for_status()
            entries = res_entries.json()

            # 2. 処置データ (Treatments)
            treatments_params = {
                **common_params,
                "find[created_at][$gte]": utc_start.isoformat(),
                "find[created_at][$lt]": utc_end.isoformat(),
            }
            res_treatments = requests.get(f"{self.url}/api/v1/treatments.json", headers=self.headers, params=treatments_params, timeout=10)
            res_treatments.raise_for_status()
            treatments = res_treatments.json()

            return entries, treatments

        except requests.exceptions.RequestException as e:
            logger.error(f"Nightscout通信エラー: {e}")
            raise

class DataProcessor:
    """データの整形と解析ロジック"""

    @staticmethod
    def get_direction_arrow(direction: Optional[str]) -> str:
        arrows = {
            'DoubleUp': '⇈', 'SingleUp': '↑', 'FortyFiveUp': '↗',
            'Flat': '→', 'FortyFiveDown': '↘', 'SingleDown': '↓',
            'DoubleDown': '⇊', 'NOT COMPUTABLE': '?', 'RATE OUT OF RANGE': '?'
        }
        return arrows.get(direction, '') if direction else ''

    @staticmethod
    def parse_notes(notes: str) -> Dict[str, Any]:
        """ノート欄のパース処理（既存ロジックを維持しつつ整理）"""
        result = {
            "cir": None, "predicted_insulin": None, "insulin_type": None,
            "foods": [], "basal_amount": None
        }
        
        if not notes:
            return result

        lines = [line.strip() for line in notes.strip().split('\n') if line.strip()]
        if not lines:
            return result

        first_line = lines[0]
        foods = result["foods"]

        # パターンマッチング処理
        if first_line.startswith(('Tore ', 'トレ ')):
            foods.append('基礎インスリン')
            parts = first_line.split()
            if len(parts) >= 2:
                try: result["basal_amount"] = float(parts[1])
                except ValueError: pass
            foods.extend(lines[1:])
            
        elif first_line.upper() == 'B':
            foods.append('ぶどう糖補食')
            foods.extend(lines[1:])
            
        elif first_line.upper() in ['N', 'F']:
            result["insulin_type"] = first_line.upper()
            foods.extend(lines[1:])
            
        else:
            # CIRフォーマット解析 (例: "300 4.5N")
            test_line = first_line.replace('cir', '').replace('CIR', '').strip()
            parts = test_line.split()
            
            is_cir_format = False
            if parts:
                try:
                    float(parts[0])
                    is_cir_format = True
                except ValueError:
                    pass

            if is_cir_format:
                try: result["cir"] = float(parts[0])
                except ValueError: pass
                
                if len(parts) >= 2:
                    second_part = parts[1]
                    if second_part[-1].upper() in ['N', 'F']:
                        result["insulin_type"] = second_part[-1].upper()
                        insulin_val = second_part[:-1]
                    else:
                        insulin_val = second_part
                    
                    try: result["predicted_insulin"] = float(insulin_val)
                    except ValueError: pass
                
                foods.extend(lines[1:])
            else:
                foods.extend(lines)

        return result

    @staticmethod
    def process_report_data(entries: List[Dict], treatments: List[Dict]) -> Dict[str, Any]:
        """フロントエンド用にデータを整形"""
        
        # 1. チャート用データ作成
        chart_data = {"times": [], "bgs": []}
        entries_sorted = sorted(entries, key=lambda x: x['dateString'])
        
        for e in entries_sorted:
            try:
                # 文字列処理ではなくdatetimeパースを使用
                dt_utc = datetime.fromisoformat(e['dateString'].replace('Z', '+00:00'))
                dt_jst = dt_utc.astimezone(JST)
                bg = e.get('sgv')
                if bg is not None:
                    chart_data["times"].append(dt_jst.strftime('%H:%M'))
                    chart_data["bgs"].append(bg)
            except (ValueError, TypeError):
                continue

        # 2. 統計計算
        bg_values = [e['sgv'] for e in entries if e.get('sgv') is not None]
        stats = {
            "avg_bg": round(sum(bg_values) / len(bg_values)) if bg_values else 0,
            "total_insulin": 0.0,
            "basal_insulin": 0.0,
            "total_carbs": 0.0,
        }

        # 3. テーブルデータ作成
        table_rows = []
        treatments_sorted = sorted(treatments, key=lambda x: x.get('created_at', ''))

        for treatment in treatments_sorted:
            try:
                dt_utc = datetime.fromisoformat(treatment.get('created_at', '').replace('Z', '+00:00'))
                dt_jst = dt_utc.astimezone(JST)
                
                # --- ノート解析 ---
                parsed = DataProcessor.parse_notes(treatment.get('notes', ''))
                
                # --- インスリン・糖質集計 ---
                carbs_str = treatment.get('carbs', '')
                insulin_str = treatment.get('insulin', '')
                
                # 糖質の数値化と集計
                if carbs_str:
                    try:
                        carbs_val = float(carbs_str)
                        stats["total_carbs"] += carbs_val
                        # 自動補食判定 (1-3g)
                        if 1 <= carbs_val <= 3:
                            has_hosyoku = any('補食' in f for f in parsed["foods"])
                            if not has_hosyoku:
                                parsed["foods"].insert(0, '補食')
                    except ValueError: pass

                # インスリンの数値化と集計
                is_basal = any('基礎インスリン' == f for f in parsed["foods"])
                if is_basal and parsed["basal_amount"]:
                    stats["basal_insulin"] += parsed["basal_amount"]
                elif insulin_str:
                    try: stats["total_insulin"] += float(insulin_str)
                    except ValueError: pass

                # --- CGMデータの紐付け（簡易版） ---
                bg_display = ""
                # ここで最も近いEntryを探すロジック（省略せず実装）
                # (パフォーマンスのため、本来は二分探索などが望ましいが、今回は線形探索で維持)
                if entries:
                    closest = min(entries, key=lambda x: abs(
                        (datetime.fromisoformat(x['dateString'].replace('Z', '+00:00')).timestamp() - dt_utc.timestamp())
                    ))
                    if abs((datetime.fromisoformat(closest['dateString'].replace('Z', '+00:00')).timestamp() - dt_utc.timestamp())) < 900: # 15分以内
                        cgm_val = closest.get('sgv')
                        if cgm_val:
                            arrow = DataProcessor.get_direction_arrow(closest.get('direction'))
                            delta = closest.get('delta', 0)
                            delta_str = f" ({'+' if delta > 0 else ''}{round(delta)})" if delta else ""
                            bg_display = f"{cgm_val}{delta_str} {arrow}"

                # 実測値
                if treatment.get('glucose'):
                    bg_display = f"{bg_display} / 実測:{treatment.get('glucose')}" if bg_display else f"実測:{treatment.get('glucose')}"

                table_rows.append({
                    'time': dt_jst.strftime('%H:%M'),
                    'bg': bg_display or "-",
                    'cir': parsed["cir"] or "-",
                    'carbs': f"{carbs_str}g" if carbs_str else "-",
                    'predicted': parsed["predicted_insulin"] or "-",
                    'actual': insulin_str or "-",
                    'type': parsed["insulin_type"] or "-",
                    'food': ", ".join(parsed["foods"]) if parsed["foods"] else "-"
                })

            except Exception as e:
                logger.error(f"行データ処理エラー: {e}")
                continue

        # TCIR計算
        stats["tcir"] = f"{stats['total_carbs'] / stats['total_insulin']:.1f}" if stats['total_insulin'] > 0 else "-"
        
        # 数値の丸め
        stats["total_insulin"] = round(stats["total_insulin"], 2)
        stats["basal_insulin"] = round(stats["basal_insulin"], 2)

        return {
            'chart_times': chart_data["times"],
            'chart_bgs': chart_data["bgs"],
            'table_data': table_rows,
            **stats
        }

# --- ルーティング ---

@app.route('/')
def index():
    today = datetime.now(JST).strftime("%Y-%m-%d")
    return render_template('index.html', today=today, nightscout_url=NIGHTSCOUT_URL)

@app.route('/api/report')
def get_report():
    date_str = request.args.get('date', datetime.now(JST).strftime("%Y-%m-%d"))
    
    try:
        client = NightscoutClient(NIGHTSCOUT_URL, API_SECRET)
        entries, treatments = client.fetch_data(date_str)
        report_data = DataProcessor.process_report_data(entries, treatments)
        return jsonify(report_data)
    except Exception as e:
        logger.error(f"API Error: {e}")
        return jsonify({'error': 'データの取得または処理中にエラーが発生しました'}), 500

if __name__ == '__main__':
    # ローカル開発用
    app.run(debug=True, host='0.0.0.0', port=5000)