# Local mock benchmark

Measured on 2026-09-26 on Windows with 8 logical CPUs, Docker Compose, PostgreSQL, 12 concurrent API submitters, and 100 `gemini-wrapper` tasks per run. Workers used the deterministic mock agent with a configured 100 ms delay. Each output JSON has the full timing percentiles and run settings.

| Workers | Succeeded | Elapsed | Throughput | Queue wait p50 / p95 | Submit-to-result p50 / p95 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 100/100 | 17.217 s | 5.808 tasks/s | 1,378.74 / 1,869.50 ms | 8.08 / 14.69 s |
| 2 | 100/100 | 16.832 s | 5.941 tasks/s | 433.02 / 1,160.83 ms | 7.64 / 13.94 s |

In this run, the second worker reduced median database queue wait by about 69%, while measured end-to-end throughput increased only about 2%. API submission and local Docker/database overhead dominated this small workload; adding a worker did not double throughput. The p95 measurements also vary between runs, so these values are an example and not a capacity promise.

Run details: [`workers-1.json`](workers-1.json) and [`workers-2.json`](workers-2.json). The actual worker-failure demo also completed during this session: the killed worker's task was recovered and succeeded on attempt 2, and the other 5 queued tasks succeeded. Its event timeline included `Recovered expired worker lease`.

These are local mock measurements, not a cloud benchmark. The artificial delay approximates time spent in a worker; it is not a Gemini latency measurement.
