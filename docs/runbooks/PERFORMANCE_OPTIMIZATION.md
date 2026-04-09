# Performance Optimization Runbook

**Version:** 1.0
**Last Updated:** 2026-02-26
**Scope:** FORGE revenue MVP backends
**Owner:** FORGE Engineering Team

---

## Quick Reference

### Performance Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| API p50 latency | <100ms | >200ms |
| API p95 latency | <200ms | >500ms |
| Database query | <50ms | >100ms |
| Redis hit rate | >80% | <60% |
| Memory per request | <50MB | >100MB |

### Optimization Priority Matrix

| Impact | Effort | Actions |
|--------|--------|---------|
| High | Low | Add indexes, enable caching, fix N+1 queries |
| High | Medium | Implement Redis caching, optimize queries |
| Medium | Low | Add pagination, limit result sets |
| High | High | Add task queue, refactor architecture |

---

## 1. N+1 Query Detection & Fix

### 1.1 What is N+1?

N+1 occurs when you execute 1 query to get a list of N items, then N additional queries to get related data for each item.

```python
# ❌ N+1 Pattern (1 + N queries)
users = await db.execute(select(User))
for user in users:
    orders = await db.execute(select(Order).where(Order.user_id == user.id))
    # N additional queries!
```

### 1.2 Detection Checklist

```markdown
## N+1 Detection Checklist

### Common Patterns
- [ ] Loop over query results with nested queries
- [ ] Access relationship attributes in template/view
- [ ] Load related data one item at a time
- [ ] Sequential queries that could be batched

### Tools
- [ ] Enable SQL query logging in development
- [ ] Use Django Debug Toolbar / Flask DebugToolbar
- [ ] Check query count in tests
- [ ] Profile with cProfile or py-spy
```

### 1.3 Fix Patterns

#### Pattern 1: Batch Loading

```python
# ❌ N+1 Pattern
async def get_feedback_for_responses(responses: list[Response]):
    feedbacks = []
    for response in responses:
        feedback = await db.execute(
            select(ContentFeedback).where(ContentFeedback.response_id == response.id)
        )
        feedbacks.append(feedback.scalar_one_or_none())
    return feedbacks

# ✅ Fixed: Batch loading
async def get_feedback_for_responses(responses: list[Response]):
    response_ids = [r.id for r in responses]

    # Single query for all feedbacks
    result = await db.execute(
        select(ContentFeedback).where(ContentFeedback.response_id.in_(response_ids))
    )
    all_feedbacks = result.scalars().all()

    # Create lookup map
    feedback_map = {f.response_id: f for f in all_feedbacks}

    return [feedback_map.get(r.id) for r in responses]
```

#### Pattern 2: Eager Loading (SQLAlchemy)

```python
from sqlalchemy.orm import selectinload, joinedload

# ❌ Lazy loading (N+1)
stmt = select(User)

# ✅ Eager loading with selectinload
stmt = select(User).options(
    selectinload(User.orders),
    selectinload(User.preferences)
)

# ✅ Eager loading with joinedload (single query)
stmt = select(User).options(
    joinedload(User.orders)
)
```

#### Pattern 3: JOIN Query

```python
# ❌ Two separate queries
users = await db.execute(select(User))
user_ids = [u.id for u in users]
orders = await db.execute(
    select(Order).where(Order.user_id.in_(user_ids))
)

# ✅ Single JOIN query
stmt = select(User, Order).join(
    Order, User.id == Order.user_id, isouter=True
)
result = await db.execute(stmt)
```

---

## 2. Redis Caching Guidelines

### 2.1 What to Cache

| Data Type | TTL | Cache Key Pattern | Priority |
|-----------|-----|-------------------|----------|
| User preferences | 5-10 min | `user:{id}:prefs` | High |
| Subscription status | 5 min | `user:{id}:sub` | High |
| Streak data | 1-5 min | `user:{id}:streak` | High |
| Question bank | 1 hour | `questions:all` | Medium |
| Static content | 1 hour | `content:{slug}` | Medium |
| Analytics aggregation | 5-15 min | `analytics:{id}:{date}` | Medium |
| Session feedback | 24 hours | `feedback:{session_id}` | Low |

### 2.2 Caching Implementation

```python
import json
from typing import TypeVar, Type
from redis.asyncio import Redis

T = TypeVar('T')

class CacheService:
    """Redis caching service with JSON serialization."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def get(self, key: str, model: Type[T]) -> T | None:
        """Get and deserialize cached value."""
        cached = await self.redis.get(key)
        if cached:
            return model.model_validate_json(cached)
        return None

    async def set(self, key: str, value: T, ttl: int) -> bool:
        """Serialize and cache value with TTL."""
        return await self.redis.set(
            key,
            value.model_dump_json() if hasattr(value, 'model_dump_json') else json.dumps(value),
            ex=ttl
        )

    async def delete(self, key: str) -> bool:
        """Invalidate cache key."""
        return bool(await self.redis.delete(key))

    async def delete_pattern(self, pattern: str) -> int:
        """Delete all keys matching pattern."""
        keys = []
        async for key in self.redis.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            return await self.redis.delete(*keys)
        return 0
```

### 2.3 Cache-Aside Pattern

```python
async def get_user_preferences(user_id: str, db, cache: CacheService) -> UserPreferences:
    """Get user preferences with cache-aside pattern."""
    cache_key = f"user:{user_id}:prefs"

    # Try cache first
    cached = await cache.get(cache_key, UserPreferences)
    if cached:
        return cached

    # Cache miss - load from database
    result = await db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user_id)
    )
    prefs = result.scalar_one_or_none()

    if not prefs:
        # Create default preferences
        prefs = UserPreferences(user_id=user_id)
        db.add(prefs)
        await db.commit()

    # Store in cache for next request
    await cache.set(cache_key, prefs, ttl=300)  # 5 minutes

    return prefs

async def update_user_preferences(user_id: str, updates: dict, db, cache: CacheService):
    """Update preferences and invalidate cache."""
    await db.execute(
        update(UserPreferences)
        .where(UserPreferences.user_id == user_id)
        .values(**updates)
    )
    await db.commit()

    # Invalidate cache
    await cache.delete(f"user:{user_id}:prefs")
```

### 2.4 Caching Decorator

```python
from functools import wraps
from typing import Callable, TypeVar, ParamSpec

P = ParamSpec('P')
T = TypeVar('T')

def cached(ttl: int, key_builder: Callable[..., str]):
    """Decorator for caching function results."""
    def decorator(func: Callable[P, T]) -> Callable[P, T]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            cache = get_cache_client()  # Your cache client
            key = key_builder(*args, **kwargs)

            # Try cache
            cached_value = await cache.get(key)
            if cached_value is not None:
                return cached_value

            # Execute function
            result = await func(*args, **kwargs)

            # Cache result
            await cache.set(key, result, ttl)

            return result
        return wrapper
    return decorator

# Usage
@cached(ttl=300, key_builder=lambda user_id: f"user:{user_id}:streak")
async def get_user_streak(user_id: str) -> UserStreak:
    result = await db.execute(select(UserStreak).where(UserStreak.user_id == user_id))
    return result.scalar_one()
```

---

## 3. Query Optimization Checklist

### 3.1 Index Strategy

```sql
-- When to add an index:
-- 1. Column used in WHERE clauses
-- 2. Column used in JOIN conditions
-- 3. Column used in ORDER BY
-- 4. Column used in GROUP BY

-- Composite indexes for multi-column queries
CREATE INDEX idx_user_created ON sessions(user_id, created_at DESC);

-- Partial indexes for common filters
CREATE INDEX idx_active_sessions ON sessions(user_id)
WHERE status = 'active';

-- Expression indexes for JSON queries
CREATE INDEX idx_report_overall ON sessions
USING btree ((report_json->'scores'->>'overall')::float);
```

### 3.2 Query Analysis

```python
# Enable query logging in development
import logging
logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO)

# Check query execution plan
EXPLAIN ANALYZE SELECT * FROM sessions WHERE user_id = 'abc123';

# Look for:
# - Sequential scans (should be index scans)
# - High row estimates vs actual rows
# - Sort operations that could use index
# - Nested loop joins on large tables
```

### 3.3 Query Optimization Patterns

```python
# ❌ Unbounded query - loads ALL records
stmt = select(Session).where(Session.user_id == user_id)
sessions = await db.execute(stmt)

# ✅ Limited query with pagination
stmt = select(Session).where(Session.user_id == user_id).limit(50).offset(offset)

# ❌ In-memory aggregation
sessions = await db.execute(select(Session))
total_duration = sum(s.duration for s in sessions)

# ✅ Database aggregation
stmt = select(func.sum(Session.duration)).where(Session.user_id == user_id)
total_duration = await db.scalar(stmt)

# ❌ Multiple round trips
user = await get_user(user_id)
orders = await get_orders(user_id)
prefs = await get_preferences(user_id)

# ✅ Parallel queries
import asyncio
user, orders, prefs = await asyncio.gather(
    get_user(user_id),
    get_orders(user_id),
    get_preferences(user_id)
)
```

---

## 4. Database Connection Pooling

### 4.1 SQLAlchemy Async Pool Configuration

```python
from sqlalchemy.ext.asyncio import create_async_engine

# ✅ Production configuration
engine = create_async_engine(
    settings.database_url,
    pool_size=5,           # Persistent connections
    max_overflow=10,       # Additional connections during spikes
    pool_pre_ping=True,    # Check connection health
    pool_recycle=1800,     # Recycle connections after 30 min
    echo=False,            # Disable SQL logging in production
)

# ❌ Development/testing only
from sqlalchemy.pool import NullPool
engine = create_async_engine(
    settings.database_url,
    poolclass=NullPool,    # New connection per request - OK for dev only
)
```

### 4.2 Pool Sizing Guidelines

```python
# Formula: pool_size = (core_count * 2) + disk_spindles
# For typical 4-core server: pool_size = 10

# Recommended settings by traffic level:
LOW_TRAFFIC = {"pool_size": 3, "max_overflow": 5}
MEDIUM_TRAFFIC = {"pool_size": 5, "max_overflow": 10}
HIGH_TRAFFIC = {"pool_size": 10, "max_overflow": 20}
```

---

## 5. API Response Time Optimization

### 5.1 Async Best Practices

```python
# ❌ Sequential async calls
result1 = await api_call_1()
result2 = await api_call_2(result1)
result3 = await api_call_3(result2)

# ✅ Parallel independent calls
result1, result2, result3 = await asyncio.gather(
    api_call_1(),
    api_call_2(),
    api_call_3(),
)

# ✅ Sequential dependent calls with error handling
try:
    result1 = await api_call_1()
    result2 = await asyncio.wait_for(
        api_call_2(result1),
        timeout=10.0  # Prevent hanging
    )
except asyncio.TimeoutError:
    logger.warning("api_call_2 timed out")
    result2 = fallback_value()
```

### 5.2 Response Streaming

```python
from fastapi.responses import StreamingResponse
import asyncio

async def generate_large_dataset():
    """Generator for streaming large datasets."""
    offset = 0
    batch_size = 1000

    while True:
        batch = await db.execute(
            select(DataModel).limit(batch_size).offset(offset)
        )
        records = batch.scalars().all()

        if not records:
            break

        for record in records:
            yield json.dumps(record.to_dict()) + "\n"

        offset += batch_size
        await asyncio.sleep(0)  # Yield to event loop

@router.get("/export")
async def export_data():
    """Stream large dataset instead of loading into memory."""
    return StreamingResponse(
        generate_large_dataset(),
        media_type="application/x-ndjson"
    )
```

### 5.3 Background Tasks

```python
from fastapi import BackgroundTasks

# ❌ Blocking request on slow operation
@router.post("/process")
async def process_data(data: DataInput):
    result = await slow_ml_processing(data)  # Blocks for 10+ seconds
    return {"result": result}

# ✅ Background task with polling
@router.post("/process")
async def process_data(data: DataInput, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())

    # Store task status
    await redis.set(f"task:{task_id}", json.dumps({"status": "pending"}))

    # Add to background tasks
    background_tasks.add_task(process_in_background, task_id, data)

    return {"task_id": task_id, "status": "pending"}

@router.get("/process/{task_id}")
async def get_task_status(task_id: str):
    status = await redis.get(f"task:{task_id}")
    return json.loads(status)

async def process_in_background(task_id: str, data: DataInput):
    try:
        result = await slow_ml_processing(data)
        await redis.set(f"task:{task_id}", json.dumps({
            "status": "complete",
            "result": result
        }), ex=3600)  # Keep for 1 hour
    except Exception as e:
        await redis.set(f"task:{task_id}", json.dumps({
            "status": "failed",
            "error": str(e)
        }), ex=3600)
```

---

## 6. Memory Optimization

### 6.1 Memory Profiling

```python
import tracemalloc
import asyncio

async def profile_memory_usage():
    """Profile memory usage of a function."""
    tracemalloc.start()

    # Take snapshot before
    snapshot1 = tracemalloc.take_snapshot()

    # Execute function
    await expensive_function()

    # Take snapshot after
    snapshot2 = tracemalloc.take_snapshot()

    # Compare
    top_stats = snapshot2.compare_to(snapshot1, 'lineno')
    for stat in top_stats[:10]:
        print(stat)

    tracemalloc.stop()
```

### 6.2 Memory Optimization Patterns

```python
# ❌ Load entire file into memory
content = await file.read()
process_content(content)

# ✅ Stream file processing
async for chunk in file.chunks():
    await process_chunk(chunk)

# ❌ Load all records into list
records = list(await db.execute(select(LargeTable)))

# ✅ Use server-side cursor
async for record in await db.stream(select(LargeTable)):
    await process_record(record)

# ❌ Build large list in memory
results = []
for item in large_dataset:
    results.append(transform(item))

# ✅ Use generator
async def transform_items(dataset):
    async for item in dataset:
        yield transform(item)
```

---

## 7. Project-Specific Optimizations

### 7.1 Interview Simulator

**Current Bottleneck:** N+1 queries in `feedback_service.py`

```python
# Location: app/services/feedback_service.py:173-183

# ❌ Current: N+1 pattern
for response in responses:
    existing_feedback = await session.exec(
        select(ContentFeedback).where(ContentFeedback.response_id == response.id)
    )
    if not existing_feedback.first() and response.transcript:
        await self.generate_feedback(session, response.id)

# ✅ Fixed: Batch loading
response_ids = [r.id for r in responses]
existing_result = await session.exec(
    select(ContentFeedback).where(ContentFeedback.response_id.in_(response_ids))
)
existing_feedback_map = {cf.response_id: cf for cf in existing_result.all()}

for response in responses:
    if response.id not in existing_feedback_map and response.transcript:
        await self.generate_feedback(session, response.id)
```

**Expected Improvement:** 60% latency reduction for feedback aggregation

### 7.2 Voice Coach

**Current Bottleneck:** No caching for user data

```python
# Add caching to frequently accessed data

# Priority 1: User preferences (5 min cache)
@cached(ttl=300, key_builder=lambda user_id: f"vc:user:{user_id}:profile")
async def get_user_preferences(user_id: str) -> UserPreferences:
    ...

# Priority 2: Streak data (1 min cache)
@cached(ttl=60, key_builder=lambda user_id: f"vc:user:{user_id}:streak")
async def get_streak(user_id: str) -> UserStreak:
    ...

# Priority 3: Quota status (1 min cache)
@cached(ttl=60, key_builder=lambda user_id: f"vc:user:{user_id}:quota:{month}")
async def get_quota_status(user_id: str, month: str) -> QuotaStatus:
    ...
```

**Expected Improvement:** 40% overall latency reduction

### 7.3 Study Flow

**Current Bottleneck:** Unbounded streak calculation

```python
# ❌ Current: Loads ALL reviews
stmt = select(ReviewLog.created_at).where(
    ReviewLog.user_id == user_id
).order_by(ReviewLog.created_at.desc())
reviews = await db.execute(stmt)

# ✅ Fixed: Limit to relevant period
from datetime import datetime, timedelta

cutoff = datetime.now(timezone.utc) - timedelta(days=365)
stmt = select(func.date(ReviewLog.created_at)).where(
    and_(
        ReviewLog.user_id == user_id,
        ReviewLog.created_at >= cutoff
    )
).distinct().order_by(func.date(ReviewLog.created_at).desc())
```

**Expected Improvement:** 70% faster streak calculation

---

## 8. Monitoring & Alerting

### 8.1 Metrics to Track

```yaml
# Prometheus metrics
metrics:
  # Request latency
  - name: http_request_duration_seconds
    type: histogram
    buckets: [0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0]
    labels: [method, endpoint, status]

  # Database query time
  - name: db_query_duration_seconds
    type: histogram
    buckets: [0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
    labels: [query_type, table]

  # Cache hit rate
  - name: cache_hits_total
    type: counter
    labels: [cache_name, result]  # result: hit, miss

  # Active connections
  - name: db_connections_active
    type: gauge
    labels: [pool_name]
```

### 8.2 Alert Rules

```yaml
# Alerting rules
groups:
  - name: performance
    rules:
      - alert: HighAPILatency
        expr: |
          histogram_quantile(0.95,
            rate(http_request_duration_seconds_bucket[5m])
          ) > 0.5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High API latency (p95 > 500ms)"

      - alert: LowCacheHitRate
        expr: |
          rate(cache_hits_total{result="hit"}[5m]) /
          rate(cache_hits_total[5m]) < 0.6
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Low cache hit rate (< 60%)"

      - alert: DatabaseSlowQueries
        expr: |
          histogram_quantile(0.95,
            rate(db_query_duration_seconds_bucket[5m])
          ) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Slow database queries (p95 > 100ms)"

      - alert: DatabaseConnectionPoolExhausted
        expr: db_connections_active / db_connections_max > 0.9
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Database connection pool nearly exhausted"
```

### 8.3 Dashboard Queries

```promql
# Average request latency by endpoint
rate(http_request_duration_seconds_sum[5m]) /
rate(http_request_duration_seconds_count[5m])
by (endpoint)

# Cache hit rate
sum(rate(cache_hits_total{result="hit"}[5m])) /
sum(rate(cache_hits_total[5m]))

# Database query percentiles
histogram_quantile(0.50, rate(db_query_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(db_query_duration_seconds_bucket[5m]))
histogram_quantile(0.99, rate(db_query_duration_seconds_bucket[5m]))

# Requests per second
rate(http_request_duration_seconds_count[1m])
```

---

## 9. Performance Testing

### 9.1 Load Testing with Locust

```python
# locustfile.py
from locust import HttpUser, task, between

class APIUser(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        """Login and get auth token."""
        response = self.client.post("/api/v1/auth/login", json={
            "email": "test@example.com",
            "password": "test-password-123"
        })
        self.token = response.json()["access_token"]

    @task(3)
    def get_sessions(self):
        """Get user sessions (most common)."""
        self.client.get(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {self.token}"}
        )

    @task(1)
    def create_session(self):
        """Create new session."""
        self.client.post(
            "/api/v1/sessions",
            headers={"Authorization": f"Bearer {self.token}"},
            json={"type": "practice"}
        )
```

### 9.2 Benchmark Script

```python
import asyncio
import time
from statistics import mean, median

async def benchmark_endpoint(endpoint: str, iterations: int = 100):
    """Benchmark an endpoint's response time."""
    times = []

    async with aiohttp.ClientSession() as session:
        for _ in range(iterations):
            start = time.perf_counter()
            async with session.get(endpoint) as response:
                await response.json()
            times.append(time.perf_counter() - start)

    return {
        "mean": mean(times) * 1000,  # ms
        "median": median(times) * 1000,
        "min": min(times) * 1000,
        "max": max(times) * 1000,
        "p95": sorted(times)[int(len(times) * 0.95)] * 1000,
    }

# Usage
results = await benchmark_endpoint("http://localhost:8000/api/v1/sessions")
print(f"Mean: {results['mean']:.2f}ms, P95: {results['p95']:.2f}ms")
```

---

## 10. Optimization Checklist

### Pre-Launch Performance Checklist

```markdown
## Performance Checklist

### Database
- [ ] All foreign keys indexed
- [ ] Common query columns indexed
- [ ] Composite indexes for multi-column queries
- [ ] Connection pooling enabled
- [ ] Query logging enabled in staging

### Caching
- [ ] Redis configured and tested
- [ ] User preferences cached
- [ ] Static content cached
- [ ] Cache invalidation implemented
- [ ] Cache hit rate monitored

### API
- [ ] Pagination on all list endpoints
- [ ] Rate limiting enabled
- [ ] Request size limits set
- [ ] Background tasks for slow operations
- [ ] Response compression enabled

### Monitoring
- [ ] Latency metrics collected
- [ ] Slow query alerts configured
- [ ] Cache hit rate tracked
- [ ] Memory usage monitored
- [ ] Error rate alerts set

### Testing
- [ ] Load test completed (target RPS)
- [ ] Stress test completed (breaking point)
- [ ] Endurance test completed (memory leaks)
- [ ] Benchmark comparison vs baseline
```

---

## Resources

### Internal References
- `.forge/heartbeat/results/glm-performance-audit.md` - Voice Coach analysis
- `docs/SECURITY_PERFORMANCE_FIXES.md` - Combined fixes

### External Resources
- [SQLAlchemy Performance](https://docs.sqlalchemy.org/en/20/faq/performance.html)
- [FastAPI Performance](https://fastapi.tiangolo.com/deployment/concepts/)
- [Redis Best Practices](https://redis.io/docs/management/optimization/)
- [PostgreSQL Performance](https://www.postgresql.org/docs/current/performance-tips.html)

---

**Last Updated:** 2026-02-26
**Next Review:** 2026-03-26
