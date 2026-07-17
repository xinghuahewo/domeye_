from datetime import datetime, timezone

from flask_restful import Resource


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class HealthzResource(Resource):
    """
    轻量存活探针
    Endpoint: /api/v1/healthz
    """

    def get(self):
        return {
            "status": "ok",
            "service": "domeye-backend",
            "time": _utc_now_iso(),
        }, 200
