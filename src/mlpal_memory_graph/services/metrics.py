"""In-process request metrics (memory v7 WP11): latency histograms and counters per route and
status, rendered in the Prometheus text exposition format at ``GET /metrics``. No dependency, no
tenant data, no cardinality beyond (route, method, status). Decided before the benchmark work so
every later package is measured, not remembered.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field

BUCKETS_MS: tuple[float, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


@dataclass
class _Series:
    count: int = 0
    sum_ms: float = 0.0
    buckets: list[int] = field(default_factory=lambda: [0] * (len(BUCKETS_MS) + 1))  # +Inf last
    samples: list[float] = field(default_factory=list)  # bounded ring for p50/p95

    def observe(self, ms: float) -> None:
        self.count += 1
        self.sum_ms += ms
        for i, b in enumerate(BUCKETS_MS):
            if ms <= b:
                self.buckets[i] += 1
                break
        else:
            self.buckets[-1] += 1
        self.samples.append(ms)
        if len(self.samples) > 1000:
            del self.samples[: len(self.samples) - 1000]

    def quantile(self, q: float) -> float | None:
        if not self.samples:
            return None
        s = sorted(self.samples)
        return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


class Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._series: dict[tuple[str, str, str], _Series] = defaultdict(_Series)
        # memory v9 round 3: named counters with a few low-cardinality labels (client, reason, leg)
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)

    def inc(self, name: str, n: int = 1, **labels: str) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += n

    def counter(self, name: str, **labels: str) -> int:
        with self._lock:
            return self._counters.get((name, tuple(sorted(labels.items()))), 0)

    def counters(self) -> dict[str, int]:
        with self._lock:
            return {f"{n}{{{','.join(f'{k}={v}' for k, v in lbl)}}}": c for (n, lbl), c in self._counters.items()}

    def observe(self, *, route: str, method: str, status: int, ms: float) -> None:
        with self._lock:
            self._series[(route, method, str(status))].observe(ms)

    def snapshot(self) -> dict[str, dict]:
        """{route: {count, p50_ms, p95_ms, mean_ms}} across methods and statuses — for reports."""
        out: dict[str, _Series] = defaultdict(_Series)
        with self._lock:
            for (route, _m, _s), ser in self._series.items():
                agg = out[route]
                agg.count += ser.count; agg.sum_ms += ser.sum_ms; agg.samples.extend(ser.samples)
        return {r: {"count": s.count, "p50_ms": s.quantile(0.5), "p95_ms": s.quantile(0.95),
                    "mean_ms": (s.sum_ms / s.count if s.count else None)} for r, s in out.items()}

    def render(self) -> str:
        lines = ["# HELP memory_http_request_duration_ms Request latency by route.",
                 "# TYPE memory_http_request_duration_ms histogram",
                 "# HELP memory_http_requests_total Requests by route and status.",
                 "# TYPE memory_http_requests_total counter"]
        with self._lock:
            for (route, method, status), ser in sorted(self._series.items()):
                lbl = f'route="{route}",method="{method}",status="{status}"'
                cum = 0
                for b, n in zip(BUCKETS_MS, ser.buckets[:-1], strict=True):
                    cum += n
                    lines.append(f'memory_http_request_duration_ms_bucket{{{lbl},le="{b:g}"}} {cum}')
                cum += ser.buckets[-1]
                lines.append(f'memory_http_request_duration_ms_bucket{{{lbl},le="+Inf"}} {cum}')
                lines.append(f"memory_http_request_duration_ms_sum{{{lbl}}} {ser.sum_ms:.3f}")
                lines.append(f"memory_http_request_duration_ms_count{{{lbl}}} {ser.count}")
                lines.append(f"memory_http_requests_total{{{lbl}}} {ser.count}")
        with self._lock:
            names = sorted({n for n, _ in self._counters})
            for n in names:
                lines.append(f"# TYPE memory_{n}_total counter")
                for (cn, lbl), c in sorted(self._counters.items()):
                    if cn == n:
                        lab = ",".join(f'{k}="{v}"' for k, v in lbl)
                        lines.append(f"memory_{n}_total{{{lab}}} {c}")
        return "\n".join(lines) + "\n"


REGISTRY = Registry()
