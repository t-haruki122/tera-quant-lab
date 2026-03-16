"""アプリケーションのシステム統計情報管理"""

from datetime import datetime
from collections import defaultdict

class AppStats:
    def __init__(self):
        # 外部API (yfinance etc) とキャッシュの統計
        self.api_calls = 0
        self.cache_hits = 0
        
        # サーバーリクエストの統計
        self.total_requests = 0
        self.total_response_time = 0.0
        self.errors = 0
        self.endpoint_hits = defaultdict(int)
        
        self.started_at = datetime.utcnow()

    def log_api_call(self):
        """外部APIへのアクセスを記録"""
        self.api_calls += 1

    def log_cache_hit(self):
        """キャッシュからのデータ取得を記録"""
        self.cache_hits += 1

    def log_request(self, endpoint: str, response_time: float, status_code: int):
        """APIサーバーへのリクエストを記録"""
        self.total_requests += 1
        self.total_response_time += response_time
        
        # パスパラメータを正規化 (例: /stock/AAPL -> /stock/{symbol})
        base_endpoint = "/" + endpoint.strip("/").split("/")[0] if endpoint != "/" else "/"
        if endpoint.startswith("/stock/") and len(endpoint.split("/")) >= 3:
             parts = endpoint.split("/")
             if len(parts) == 4:
                 base_endpoint = f"/stock/{{symbol}}/{parts[3]}"
             else:
                 base_endpoint = f"/stock/{{symbol}}"
        elif endpoint.startswith("/user/") and len(endpoint.split("/")) >= 3:
             parts = endpoint.split("/")
             if len(parts) >= 4 and parts[3] == "lists":
                 base_endpoint = "/user/{id}/lists/..."
             else:
                 base_endpoint = "/user/{id}"
        else:
             base_endpoint = endpoint

        self.endpoint_hits[base_endpoint] += 1
        
        if status_code >= 400:
            self.errors += 1

    def get_stats(self) -> dict:
        """現在の統計情報を取得"""
        # データ取得におけるキャッシュヒット率
        # (過去の互換性のためtotal_requestsと混同しないよう注意)
        data_total = self.api_calls + self.cache_hits
        hit_rate = 0.0
        if data_total > 0:
            hit_rate = round((self.cache_hits / data_total) * 100, 1)

        # 平均レスポンスタイム (ms)
        avg_response_time = 0.0
        if self.total_requests > 0:
            avg_response_time = round((self.total_response_time / self.total_requests) * 1000, 2)
            
        # エラー率
        error_rate = 0.0
        if self.total_requests > 0:
            error_rate = round((self.errors / self.total_requests) * 100, 1)

        # アクセス数の多いエンドポイント上位5件
        top_endpoints = sorted(
            [{"endpoint": k, "count": v} for k, v in self.endpoint_hits.items()],
            key=lambda x: x["count"],
            reverse=True
        )[:5]

        # 既存プロパティ名の互換性を維持しつつ拡張
        return {
            "api_calls": self.api_calls,
            "cache_hits": self.cache_hits,
            "total_requests": data_total, # 既存フロントエンド互換用
            "hit_rate_percent": hit_rate,
            
            "server_requests": self.total_requests,
            "server_errors": self.errors,
            "error_rate_percent": error_rate,
            "avg_response_time_ms": avg_response_time,
            "top_endpoints": top_endpoints,
            
            "uptime_seconds": int((datetime.utcnow() - self.started_at).total_seconds())
        }

# グローバルな統計インスタンス
stats = AppStats()
