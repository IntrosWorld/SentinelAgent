"""Measure direct versus proxied TLS against a local test certificate/server."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import platform
import tempfile
import time

import httpx

from sentinel_agent.egress.lab import tls_lab
from sentinel_agent.egress.telemetry import percentile


async def measure(samples=50, payload_bytes=4 * 1024 * 1024):
    if samples < 2 or payload_bytes < 1:
        raise ValueError("Need at least 2 samples and a positive payload")
    direct, proxied = [], []
    with tempfile.TemporaryDirectory() as directory:
        async with tls_lab(Path(directory), "latency") as (proxy, url, context):
            # Same numeric destination on both paths avoids localhost IPv6
            # Happy Eyeballs fallback being counted as proxy performance.
            url = url.replace("localhost", "127.0.0.1")
            async with httpx.AsyncClient(verify=context, trust_env=False) as plain, httpx.AsyncClient(
                verify=context, trust_env=False, proxy=proxy.url
            ) as via_proxy:
                for i in range(samples + 3):
                    # Alternate pair order to reduce warm-up/order bias.
                    pairs = [(plain, direct), (via_proxy, proxied)]
                    if i % 2:
                        pairs.reverse()
                    for client, results in pairs:
                        start = time.perf_counter()
                        response = await client.get(url)
                        response.raise_for_status()
                        elapsed = (time.perf_counter() - start) * 1000
                        if i >= 3:
                            results.append(elapsed)
        async with tls_lab(Path(directory), "throughput", payload_bytes) as (proxy, url, context):
            url = url.replace("localhost", "127.0.0.1")
            async with httpx.AsyncClient(verify=context, trust_env=False, proxy=proxy.url, timeout=30) as client:
                start = time.perf_counter()
                response = await client.get(url)
                response.raise_for_status()
                duration = time.perf_counter() - start
                if len(response.content) != payload_bytes:
                    raise RuntimeError("Incomplete throughput payload")
    delta = [p - d for p, d in zip(proxied, direct)]
    return {"machine": platform.node(), "os": platform.platform(), "python": platform.python_version(),
            "processor": platform.processor(), "logical_cpus": os.cpu_count(), "samples": samples,
            "warmup_pairs": 3, "method": "IPv4 loopback TLS fresh connection per request; paired order alternates; excludes DLP",
            "direct_p50_ms": round(percentile(direct, .5), 3), "direct_p95_ms": round(percentile(direct, .95), 3),
            "proxy_p50_ms": round(percentile(proxied, .5), 3), "proxy_p95_ms": round(percentile(proxied, .95), 3),
            "added_p50_ms": round(percentile(delta, .5), 3), "added_p95_ms": round(percentile(delta, .95), 3),
            "payload_bytes": payload_bytes, "throughput_MB_s": round(payload_bytes / 1_000_000 / duration, 3),
            "direct_samples_ms": direct, "proxy_samples_ms": proxied}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(measure(args.samples))
    output = json.dumps(result, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
