# Service Specification: Analytics (PostHog)

## Overview
The Analytics service provides a unified interface for tracking user behavior using PostHog. It supports asynchronous event capturing and automatic batching.

## Interface: `PostHogClient`

### Configuration
- `api_key`: PostHog API Key.
- `host`: PostHog Host (default: `https://app.posthog.com`).
- `enabled`: Toggle analytics (default: `True`).
- `flush_interval`: Seconds between automatic flushes (default: 30).
- `flush_at`: Events threshold for automatic flush (default: 20).

### Methods

#### `track` (Async)
Captures a custom event.
- **Parameters**: `event` (name), `distinct_id`, `properties` (dict), `timestamp` (optional).

#### `identify` (Async)
Identifies a user with specific traits.
- **Parameters**: `distinct_id`, `properties` (dict).

#### `alias` (Async)
Connects two different IDs for the same user.
- **Parameters**: `distinct_id`, `alias`.

#### `flush` (Async)
Manually flushes the event queue to PostHog.

#### `shutdown`
Flushes and shuts down the client.

## Standard Events
Projects are encouraged to use standardized event names:
- `user_signed_up`
- `user_logged_in`
- `interview_started`
- `content_generated`
- `plan_upgraded`
