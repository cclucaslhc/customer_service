"""Synthetic HTTP observations through the real private metrics listener."""
import asyncio
from app.http_observability import HttpTelemetry
from app.private_metrics import MetricsPolicy,private_metrics_listener

TOKEN=b'synthetic-grafana-metrics-only-01234567890123456789'


async def main():
    telemetry=HttpTelemetry()
    async with private_metrics_listener(telemetry,MetricsPolicy(token=TOKEN,port=9107)):
        while True:
            telemetry.record(route='/synthetic/{item_id}',method='GET',status=200,
                outcome='completed',stream=False,duration=.1,request_id='synthetic-not-a-label')
            await asyncio.sleep(1)


if __name__=='__main__': asyncio.run(main())
