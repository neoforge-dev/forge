---
name: pwa-frontend-lite
description: Build lightweight PWA frontends with React/Vite (default) or HTMX/Lit, ensuring offline readiness and consistent UX patterns.
---


# PWA Frontend Lite

## When to Use
- Implementing web frontends for MVPs that need offline-first capabilities
- Building Progressive Web Apps for mobile-first experiences
- Creating lightweight frontends (adguild.io, leanvibe.ai, mumchef.io, etc.)

## Reference Material
- `complete-mvp-docs.md` sections per domain for UI flows and prompts
- `all-projects.md` quick reference for user journeys
- Domain policies for UX/legal copy (BabyBites safety messaging, CalmConnect anonymity, etc.)
- `docs/10-build-process.md` delivery rhythm + testing expectations
- Reference implementation: `cmd/forged/` (HTMX UI templates at `:8081/ui`)

## Tech Stack Decision

**Default: React + Vite + Tailwind CSS**

| When | Choice | Rationale |
|------|--------|-----------|
| **Default** | React + Vite | Ecosystem, agent familiarity, shared patterns |
| Complex dashboards | React + Vite | State management, charts, forms |
| Simple landing pages | Lit PWA | Minimal JS, web components |
| Existing Lit projects | Lit PWA | Don't migrate unless necessary |

## 1. Project Structure

### React + Vite PWA (Default)

```
project/
├── public/
│   ├── sw.js                 # Service worker (PLAIN JS ONLY)
│   ├── offline.html          # Offline fallback page
│   ├── manifest.webmanifest  # PWA manifest
│   ├── pwa-192x192.png       # App icon (192x192)
│   ├── pwa-512x512.png       # App icon (512x512)
│   └── apple-touch-icon.png  # iOS icon
├── src/
│   ├── api/
│   │   └── client.ts         # API client with offline handling
│   ├── components/
│   │   ├── ui/               # Generic components (Button, Modal)
│   │   └── [feature]/        # Feature-specific components
│   ├── pages/                # Route components
│   ├── hooks/                # Custom React hooks
│   ├── stores/               # Zustand stores
│   ├── types/                # TypeScript types
│   ├── utils/                # Helpers, formatters
│   ├── App.tsx               # Root component
│   └── main.tsx              # Entry point
├── index.html
├── vite.config.ts            # Vite + PWA config
├── tailwind.config.js
├── package.json
└── tsconfig.json
```

### Lit PWA (Simple Projects)

```
project/
├── public/
│   ├── sw.js
│   ├── offline.html
│   └── manifest.webmanifest
├── src/
│   ├── components/
│   │   ├── app-shell.ts      # Main app component
│   │   └── [feature]/        # Feature components
│   ├── styles/
│   │   └── main.css
│   └── index.ts
├── index.html
└── vite.config.ts
```

## 2. Manifest Template

Complete `manifest.webmanifest` for PWA:

```json
{
  "name": "Your App Name",
  "short_name": "App",
  "description": "Your app description",
  "theme_color": "#1e40af",
  "background_color": "#0f172a",
  "display": "standalone",
  "orientation": "portrait-primary",
  "scope": "/",
  "start_url": "/",
  "icons": [
    {
      "src": "pwa-192x192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "pwa-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any"
    },
    {
      "src": "pwa-512x512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "maskable"
    }
  ],
  "categories": ["productivity", "utilities"],
  "shortcuts": [
    {
      "name": "Dashboard",
      "short_name": "Dashboard",
      "description": "View your dashboard",
      "url": "/",
      "icons": [{ "src": "pwa-192x192.png", "sizes": "192x192" }]
    }
  ]
}
```

**Key Fields:**
- `display: "standalone"` - Hides browser UI
- `orientation: "portrait-primary"` - Lock orientation (optional)
- `purpose: "maskable"` - Adaptive icon for Android
- `shortcuts` - Quick actions for home screen

## 3. Service Worker

Offline-first caching strategies using plain JavaScript.

**Location:** `public/sw.js` (MUST be plain JS, NO TypeScript)

### Cache Strategies

```javascript
/// <reference lib="webworker" />
/* eslint-disable no-restricted-globals */

// Build timestamp injected by Vite
const BUILD_TIME = (typeof __BUILD_TIME__ !== 'undefined') ? __BUILD_TIME__ : 'dev'
const CACHE_VERSION = BUILD_TIME
const CACHE_NAME = `app-${CACHE_VERSION}`

// Cache names for different strategies
const STATIC_CACHE = `${CACHE_NAME}-static`
const API_CACHE = `${CACHE_NAME}-api`
const IMAGE_CACHE = `${CACHE_NAME}-images`

// Cache age limits (in seconds)
const STATIC_AGE_LIMIT = 7 * 24 * 60 * 60  // 7 days
const API_AGE_LIMIT = 60 * 60              // 1 hour
const IMAGE_AGE_LIMIT = 30 * 24 * 60 * 60  // 30 days

// Assets to precache on install
const PRECACHE_ASSETS = [
  '/',
  '/manifest.webmanifest',
  '/offline.html'
]

// Install event - precache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(STATIC_CACHE)
      await cache.addAll(PRECACHE_ASSETS)
      await self.skipWaiting()
    })()
  )
})

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      // Enable navigation preload if available
      if ('navigationPreload' in self.registration) {
        await self.registration.navigationPreload.enable()
      }

      // Clean up old caches
      const cacheNames = await caches.keys()
      const cachesToDelete = cacheNames.filter(
        (name) => name.startsWith('app-') && !name.startsWith(CACHE_NAME)
      )
      await Promise.all(cachesToDelete.map((name) => caches.delete(name)))
      await self.clients.claim()
    })()
  )
})

// Fetch event - handle routing with different strategies
self.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)

  if (request.method === 'GET') {
    // API calls - Network First
    if (url.pathname.startsWith('/api/')) {
      event.respondWith(networkFirst(request, API_CACHE))
      return
    }

    // Static assets - Cache First
    if (url.pathname.match(/\.(?:js|css|html|json|svg|ico|png|jpg|jpeg|webp|woff2?|ttf|eot)$/)) {
      event.respondWith(cacheFirst(request, STATIC_CACHE))
      return
    }

    // Navigation - Network First, fallback to offline page
    if (request.mode === 'navigate') {
      event.respondWith(navigationHandler(request))
      return
    }
  }
})

/** Network First - tries network first, falls back to cache */
async function networkFirst(request, cacheName) {
  const cache = await caches.open(cacheName)
  try {
    const response = await fetch(request)
    if (response.ok && response.status === 200) {
      await cache.put(request, response.clone())
    }
    return response
  } catch (error) {
    const cachedResponse = await cache.match(request)
    if (cachedResponse) return cachedResponse

    return new Response(
      JSON.stringify({ error: 'offline', message: 'No network connection' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    )
  }
}

/** Cache First - tries cache first, falls back to network */
async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName)
  const cachedResponse = await cache.match(request)

  if (cachedResponse) return cachedResponse

  try {
    const response = await fetch(request)
    if (response.ok && response.status === 200) {
      await cache.put(request, response.clone())
    }
    return response
  } catch (error) {
    return new Response('Offline', { status: 503 })
  }
}

/** Navigation handler - handles page navigation */
async function navigationHandler(request) {
  try {
    const response = await fetch(request)
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE)
      await cache.put(request, response.clone())
    }
    return response
  } catch (error) {
    const cache = await caches.open(STATIC_CACHE)
    const cachedResponse = await cache.match(request)
    if (cachedResponse) return cachedResponse

    const offlineResponse = await cache.match('/offline.html')
    if (offlineResponse) return offlineResponse

    return new Response(
      '<html><body><h1>Offline</h1></body></html>',
      { status: 503, headers: { 'Content-Type': 'text/html' } }
    )
  }
}
```

### Offline Queue (POST Requests)

```javascript
/** Handle POST requests - queue for later if offline */
async function handlePostRequest(request) {
  try {
    return await fetch(request)
  } catch (error) {
    // Store request for later sync
    const requestData = {
      url: request.url,
      method: request.method,
      headers: Object.fromEntries(request.headers.entries()),
      body: await request.text(),
      timestamp: Date.now()
    }

    await storeOfflineRequest(requestData)

    return new Response(
      JSON.stringify({ offline: true, message: 'Request queued for sync' }),
      { status: 202, headers: { 'Content-Type': 'application/json' } }
    )
  }
}

/** Store offline request in IndexedDB */
async function storeOfflineRequest(requestData) {
  const dbName = 'offline-queue'
  const storeName = 'requests'

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(dbName, 1)

    request.onsuccess = () => {
      const db = request.result
      const transaction = db.transaction([storeName], 'readwrite')
      const store = transaction.objectStore(storeName)
      store.add(requestData)
      transaction.oncomplete = () => resolve()
    }

    request.onupgradeneeded = (event) => {
      const db = event.target.result
      if (!db.objectStoreNames.contains(storeName)) {
        db.createObjectStore(storeName, { keyPath: 'id', autoIncrement: true })
      }
    }
  })
}

// Sync event - sync offline requests when online
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-offline-requests') {
    event.waitUntil(syncOfflineRequests())
  }
})
```

## 4. Component Patterns

### React Components (Default)

#### API Client with Offline Support

```typescript
// src/api/client.ts
class ApiClient {
  private baseUrl = import.meta.env.VITE_API_URL || '/api'

  async request<T>(path: string, options?: RequestInit): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...options?.headers,
      },
    })

    if (!response.ok) {
      if (response.status === 503) {
        throw new Error('offline')
      }
      throw new Error(`API error: ${response.statusText}`)
    }

    return response.json()
  }

  get<T>(path: string) {
    return this.request<T>(path)
  }

  post<T>(path: string, data: unknown) {
    return this.request<T>(path, {
      method: 'POST',
      body: JSON.stringify(data),
    })
  }
}

export const api = new ApiClient()
```

#### Using React Query for State Management

```typescript
// src/hooks/useData.ts
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'

export function useData() {
  return useQuery({
    queryKey: ['data'],
    queryFn: () => api.get('/data'),
    refetchInterval: 5000, // Poll every 5s
    retry: (failureCount, error) => {
      // Don't retry if offline
      if (error.message === 'offline') return false
      return failureCount < 3
    },
  })
}

// In component
function DataComponent() {
  const { data, isLoading, error } = useData()

  if (isLoading) return <Spinner />
  if (error) return <ErrorMessage error={error} />

  return <DataView data={data} />
}
```

### Lit Web Components (Simple Projects)

```typescript
// src/components/app-shell.ts
import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

@customElement('app-shell')
export class AppShell extends LitElement {
  static styles = css`
    :host {
      display: block;
      font-family: system-ui, sans-serif;
    }
    .container {
      max-width: 1200px;
      margin: 0 auto;
      padding: 1rem;
    }
  `

  @state()
  private items: string[] = []

  @state()
  private isOffline = !navigator.onLine

  connectedCallback() {
    super.connectedCallback()
    window.addEventListener('online', this.handleOnline)
    window.addEventListener('offline', this.handleOffline)
    this.fetchData()
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    window.removeEventListener('online', this.handleOnline)
    window.removeEventListener('offline', this.handleOffline)
  }

  private handleOnline = () => {
    this.isOffline = false
    this.fetchData()
  }

  private handleOffline = () => {
    this.isOffline = true
  }

  private async fetchData() {
    try {
      const response = await fetch('/api/items')
      const data = await response.json()
      this.items = data
    } catch (error) {
      console.error('Failed to fetch data:', error)
    }
  }

  render() {
    return html`
      <div class="container">
        ${this.isOffline ? html`<div class="offline-banner">You are offline</div>` : ''}
        <ul>
          ${this.items.map(item => html`<li>${item}</li>`)}
        </ul>
      </div>
    `
  }
}
```

## 5. HTMX Patterns

HTMX is ideal for server-rendered partial updates.

### Basic Setup

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>HTMX PWA</title>
  <script src="https://unpkg.com/htmx.org@1.9.10"></script>
  <link rel="manifest" href="/manifest.webmanifest">
</head>
<body>
  <div id="app"></div>
</body>
</html>
```

### Common Patterns

```html
<!-- Partial update on click -->
<button
  hx-get="/api/data"
  hx-target="#content"
  hx-swap="innerHTML">
  Load Data
</button>

<!-- Form with optimistic UI -->
<form
  hx-post="/api/items"
  hx-target="#items-list"
  hx-swap="beforeend">
  <input type="text" name="item" />
  <button type="submit">Add</button>
</form>

<!-- Polling for updates -->
<div
  hx-get="/api/status"
  hx-trigger="every 5s"
  hx-swap="innerHTML">
  Loading...
</div>

<!-- Infinite scroll -->
<div
  hx-get="/api/items?page=2"
  hx-trigger="revealed"
  hx-swap="afterend">
</div>
```

## 6. Build Configuration

### Vite Config with PWA

```typescript
// vite.config.ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'
import path from 'path'

const BUILD_TIMESTAMP = new Date().toISOString().replace(/[:.]/g, '-').slice(0, -5)

export default defineConfig({
  define: {
    '__BUILD_TIME__': JSON.stringify(BUILD_TIMESTAMP)
  },
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest',
      srcDir: 'public',
      filename: 'sw.js',
      manifest: {
        name: 'Your App Name',
        short_name: 'App',
        theme_color: '#1e40af',
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png'
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png'
          }
        ]
      },
      devOptions: {
        enabled: true,
        type: 'module'
      }
    })
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true
      }
    }
  }
})
```

### Package.json Scripts

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest",
    "test:e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "@tanstack/react-query": "^5.60.0",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.3.3",
    "vite": "^5.4.10",
    "vite-plugin-pwa": "^0.20.5",
    "typescript": "~5.6.2",
    "vitest": "^2.1.4",
    "@playwright/test": "^1.48.0"
  }
}
```

## 7. Testing

### Unit Tests (Vitest)

```typescript
// src/components/__tests__/Button.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { Button } from '../Button'

describe('Button', () => {
  it('renders with text', () => {
    render(<Button>Click me</Button>)
    expect(screen.getByText('Click me')).toBeInTheDocument()
  })
})
```

### E2E Tests (Playwright)

```typescript
// e2e/offline.spec.ts
import { test, expect } from '@playwright/test'

test('works offline', async ({ page, context }) => {
  // Visit app online
  await page.goto('/')
  await expect(page.locator('h1')).toContainText('Dashboard')

  // Go offline
  await context.setOffline(true)

  // Navigate to another page
  await page.click('a[href="/about"]')

  // Should show offline page
  await expect(page.locator('h1')).toContainText('Offline')

  // Go back online
  await context.setOffline(false)

  // Should reload
  await page.reload()
  await expect(page.locator('h1')).toContainText('Dashboard')
})
```

### PWA Testing Checklist

```bash
# Test service worker registration
npm run dev
# Open DevTools > Application > Service Workers
# Verify service worker is registered

# Test offline mode
# DevTools > Network > Throttling > Offline
# Navigate app, verify cached pages work

# Test manifest
# DevTools > Application > Manifest
# Verify all fields are correct

# Lighthouse audit
# DevTools > Lighthouse > Progressive Web App
# Target: 90+ score
```

## Workflow

1. **Foundation**
   - Set up Vite + React (or Lit) + Tailwind skeleton
   - Configure service worker with caching strategies
   - Add PWA manifest with icons
   - Ensure accessibility (ARIA, keyboard support) and responsive layout

2. **Critical Flow Implementation**
   - Focus on the single happy-path journey from `complete-mvp-docs.md`
   - Use React Query for data fetching and caching
   - Integrate backend endpoints with offline handling
   - Add optimistic UI and error messaging

3. **State & Storage**
   - Use Zustand for global state
   - Use localStorage/IndexedDB for client caching where required
   - Mirror server schemas for type safety

4. **Testing**
   - Add Vitest unit tests for components
   - Add Playwright E2E tests for primary flow
   - Validate offline behavior (service worker)
   - Check compliance copy using domain policy files

5. **Documentation**
   - Update domain `docs/PLAN.md` with completed UI tasks
   - Capture screenshots for stakeholders
   - Log learnings in `docs/progress.md`

## Output Checklist

- [ ] Minimal, accessible UI covering core flow
- [ ] Offline-first service worker configured
- [ ] PWA manifest with proper icons
- [ ] Tests (unit + E2E) passing
- [ ] Lighthouse PWA score 90+
- [ ] Docs updated; cross-linked in living pyramid
- [ ] Compliance messaging present where required

## Common Issues

**Service Worker Not Updating**
- Solution: Increment `BUILD_TIME` or clear cache in DevTools

**Manifest Not Loading**
- Solution: Check `<link rel="manifest">` in `index.html`
- Verify manifest is served with correct MIME type

**Icons Not Showing**
- Solution: Ensure icons are in `public/` folder
- Use 192x192 and 512x512 sizes
- Include `purpose: "maskable"` for Android

**Offline Page Not Showing**
- Solution: Verify `offline.html` is precached
- Check navigation handler in service worker
