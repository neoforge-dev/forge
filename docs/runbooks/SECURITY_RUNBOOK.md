# Security Runbook

**Version:** 2.0
**Last Updated:** 2026-02-26
**Scope:** All FORGE projects
**Owner:** FORGE Security Team

---

## Quick Reference

### Security Incident Levels

| Level | Example | Response Time | Escalation |
|-------|---------|---------------|------------|
| **P0 Critical** | Data breach, RCE, leaked prod keys | 15 min | All hands |
| **P1 High** | Auth bypass, exposed secrets | 1 hour | Security team + leads |
| **P2 Medium** | Dependency vulnerability | 24 hours | Weekly review |
| **P3 Low** | Config warning, best practice | 7 days | Monthly audit |

### Emergency Contacts

| Role | Contact | Escalation |
|------|---------|------------|
| Security Lead | security@forge.dev | CTO |
| Incident Response | incident@forge.dev | On-call |
| Compliance Officer | compliance@forge.dev | CEO |

---

## 1. Pre-Deployment Security Checklist

### 1.1 Infrastructure

```markdown
## Infrastructure Security

### Secrets Management
- [ ] Production secrets rotated (not using dev/test keys)
- [ ] No secrets in .env files on dev machines (use 1Password/Doppler)
- [ ] DATABASE_URL uses SSL (postgresql+asyncpg://...?ssl=require)
- [ ] Redis AUTH password configured
- [ ] All API keys stored in platform secrets (Railway, Cloudflare)

### Network Security
- [ ] All services behind HTTPS (no HTTP endpoints)
- [ ] WAF enabled (Cloudflare or AWS WAF)
- [ ] DDoS protection enabled
- [ ] CORS restricted to specific origins
- [ ] Rate limiting enabled

### Access Control
- [ ] Admin endpoints IP-restricted or VPN-only
- [ ] Database access restricted to application servers
- [ ] SSH key-based authentication only
- [ ] Service accounts use minimal permissions
```

### 1.2 Application

```markdown
## Application Security

### Configuration
- [ ] DEBUG=False in production
- [ ] Log level = INFO or WARN (not DEBUG)
- [ ] No development/test endpoints exposed
- [ ] Secret keys are 32+ random characters
- [ ] Default credentials removed

### Authentication
- [ ] Password complexity enforced (12+ chars, mixed case, numbers, symbols)
- [ ] Account lockout after failed attempts (5 attempts, 30 min)
- [ ] Session timeout configured (30 min access, 7 day refresh max)
- [ ] JWT signing key is unique per environment
- [ ] Refresh tokens hashed before storage

### Input/Output
- [ ] All user inputs validated (Pydantic models)
- [ ] SQL queries use ORM/parameterized (no string concatenation)
- [ ] File uploads validated (type, size, extension)
- [ ] HTML output properly escaped
- [ ] Error messages don't expose internals
```

### 1.3 Compliance

```markdown
## Compliance Requirements

### All Projects
- [ ] Privacy policy deployed and accessible
- [ ] Terms of service deployed
- [ ] Cookie consent implemented (GDPR)

### COPPA Projects (Kids Apps)
- [ ] Age gate at registration
- [ ] Parental consent workflow
- [ ] Parent dashboard for data access
- [ ] Minimal data collection
- [ ] No behavioral advertising

### HIPAA Lite (Health Apps)
- [ ] Encryption at rest (AES-256)
- [ ] Access logging for PHI
- [ ] 72-hour breach notification process
```

---

## 2. JWT Best Practices

### 2.1 Token Configuration

```python
# ✅ CORRECT - Secure JWT configuration
from datetime import timedelta
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # JWT Settings
    jwt_secret_key: str  # NO default - must be set
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30  # 30 minutes
    refresh_token_expire_days: int = 7     # 7 days max

    @property
    def access_token_expire(self) -> timedelta:
        return timedelta(minutes=self.access_token_expire_minutes)

    def validate_production(self) -> None:
        import secrets
        if len(self.jwt_secret_key) < 32:
            raise ValueError("JWT secret must be 32+ characters")
        if self.jwt_secret_key in ["change-me", "dev-secret", "secret"]:
            raise ValueError("JWT secret must not be a default value")
```

### 2.2 Token Generation

```python
from datetime import datetime, timedelta, timezone
from jose import jwt
import secrets

def create_access_token(user_id: str, secret: str, expires_delta: timedelta) -> str:
    """Create secure access token."""
    now = datetime.now(timezone.utc)
    expire = now + expires_delta

    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": now,
        "type": "access",
        "jti": secrets.token_urlsafe(16),  # Unique token ID
    }
    return jwt.encode(payload, secret, algorithm="HS256")

def create_refresh_token(user_id: str, secret: str) -> str:
    """Create secure refresh token."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(days=7)

    payload = {
        "sub": user_id,
        "exp": expire,
        "iat": now,
        "type": "refresh",
        "jti": secrets.token_urlsafe(32),  # Longer for refresh
    }
    return jwt.encode(payload, secret, algorithm="HS256")
```

### 2.3 Token Storage

```python
import hashlib

def hash_token(token: str) -> str:
    """Hash refresh token for database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

# Store hashed token in database
refresh_token = create_refresh_token(user_id, secret)
token_hash = hash_token(refresh_token)
await db.execute(
    insert(RefreshToken).values(
        user_id=user_id,
        token_hash=token_hash,  # Store hash, not plaintext!
        expires_at=expire,
    )
)
```

### 2.4 Token Validation

```python
from jose import JWTError, jwt
from fastapi import HTTPException, status

async def validate_token(token: str, secret: str, expected_type: str = "access"):
    """Validate JWT token with security checks."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])

        # Check token type
        if payload.get("type") != expected_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type"
            )

        # Check expiration (handled by jose, but explicit check)
        exp = payload.get("exp")
        if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token expired"
            )

        return payload

    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )
```

---

## 3. Stripe Webhook Security

### 3.1 Webhook Signature Verification

```python
import stripe
import hmac
import hashlib
from fastapi import Request, HTTPException, status

async def verify_stripe_webhook(
    request: Request,
    webhook_secret: str,
) -> dict:
    """Verify Stripe webhook signature."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing stripe-signature header"
        )

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            webhook_secret,
        )
        return event

    except ValueError as e:
        # Invalid payload
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payload: {str(e)}"
        )
    except stripe.error.SignatureVerificationError as e:
        # Invalid signature
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid signature: {str(e)}"
        )
```

### 3.2 Webhook Endpoint

```python
from fastapi import APIRouter, Request, BackgroundTasks

router = APIRouter()

@router.post("/webhooks/stripe")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Handle Stripe webhooks with signature verification.

    CRITICAL: Never skip signature verification in production!
    """
    event = await verify_stripe_webhook(
        request,
        settings.stripe_webhook_secret,
    )

    # Process event in background to return quickly
    background_tasks.add_task(process_stripe_event, event)

    return {"status": "received"}

async def process_stripe_event(event: dict):
    """Process Stripe event based on type."""
    event_type = event["type"]

    if event_type == "checkout.session.completed":
        await handle_checkout_complete(event["data"]["object"])
    elif event_type == "customer.subscription.updated":
        await handle_subscription_updated(event["data"]["object"])
    elif event_type == "customer.subscription.deleted":
        await handle_subscription_deleted(event["data"]["object"])
    elif event_type == "invoice.payment_failed":
        await handle_payment_failed(event["data"]["object"])
    else:
        # Log unhandled events
        logger.info(f"Unhandled Stripe event: {event_type}")
```

### 3.3 Webhook Security Checklist

```markdown
## Stripe Webhook Security

### Configuration
- [ ] Webhook secret stored in environment (STRIPE_WEBHOOK_SECRET)
- [ ] Using live webhook secret in production (whsec_...)
- [ ] Webhook endpoint is HTTPS only
- [ ] Webhook timeout configured (< 30 seconds)

### Implementation
- [ ] Signature verification on EVERY request
- [ ] Raw body used for verification (not parsed JSON)
- [ ] Background processing for slow operations
- [ ] Idempotent event handling (handle duplicates)
- [ ] All event types logged (even unhandled)

### Testing
- [ ] Test with Stripe CLI: `stripe listen --forward-to localhost:8000/webhooks/stripe`
- [ ] Test with invalid signatures (should 400)
- [ ] Test with replay attacks (should fail)
- [ ] Test duplicate events (should be idempotent)
```

---

## 4. Password Security

### 4.1 Password Hashing

```python
import bcrypt

def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    # Generate salt and hash
    salt = bcrypt.gensalt(rounds=12)  # Cost factor 12
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8")
    )
```

### 4.2 Password Complexity

```python
import re
from fastapi import HTTPException, status

def validate_password_complexity(password: str) -> None:
    """
    Enforce password complexity requirements.

    Requirements:
    - Minimum 12 characters
    - At least one uppercase letter
    - At least one lowercase letter
    - At least one digit
    - At least one special character
    """
    errors = []

    if len(password) < 12:
        errors.append("Password must be at least 12 characters")

    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain uppercase letters")

    if not re.search(r"[a-z]", password):
        errors.append("Password must contain lowercase letters")

    if not re.search(r"\d", password):
        errors.append("Password must contain numbers")

    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
        errors.append("Password must contain special characters")

    # Check for common patterns
    common_patterns = ["password", "123456", "qwerty", "abc123"]
    lower_password = password.lower()
    for pattern in common_patterns:
        if pattern in lower_password:
            errors.append(f"Password contains common pattern: {pattern}")
            break

    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": errors}
        )
```

### 4.3 Password Reset Token Security

```python
import secrets
import hashlib
from datetime import datetime, timedelta, timezone

def generate_reset_token() -> tuple[str, str]:
    """
    Generate secure password reset token.

    Returns:
        tuple: (token_to_send, token_hash_for_db)
    """
    # Generate cryptographically secure token
    token = secrets.token_urlsafe(32)

    # Hash for database storage
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    return token, token_hash

async def store_reset_token(user_id: str, token_hash: str, db):
    """Store reset token with expiration."""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    await db.execute(
        insert(PasswordReset).values(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            used=False,
        )
    )

async def validate_reset_token(token: str, db) -> str | None:
    """
    Validate reset token and return user_id if valid.

    Returns None if invalid, expired, or already used.
    """
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    result = await db.execute(
        select(PasswordReset).where(
            PasswordReset.token_hash == token_hash,
            PasswordReset.used == False,
            PasswordReset.expires_at > datetime.now(timezone.utc),
        )
    )
    reset_record = result.scalar_one_or_none()

    if not reset_record:
        return None

    # Mark as used
    reset_record.used = True
    await db.commit()

    return reset_record.user_id
```

---

## 5. Rate Limiting Security

### 5.1 Secure IP Extraction

```python
from fastapi import Request

def get_client_ip(request: Request) -> str:
    """
    Get client IP address securely.

    IMPORTANT: Only trust headers from known proxies (Cloudflare, Railway).
    """
    # Priority order for trusted headers
    # 1. Cloudflare (if behind CF)
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return sanitize_ip(cf_ip)

    # 2. Nginx/standard proxy
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return sanitize_ip(real_ip)

    # 3. X-Forwarded-For (take last trusted proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Only trust the LAST hop (your proxy)
        # If behind 1 proxy: client,proxy1 -> take proxy1 (trusted)
        # If behind 2 proxies: client,proxy1,proxy2 -> take proxy2
        parts = [p.strip() for p in forwarded.split(",")]
        if parts:
            return sanitize_ip(parts[-1])

    # 4. Direct connection
    return sanitize_ip(request.client.host if request.client else "unknown")

def sanitize_ip(ip: str) -> str:
    """Sanitize IP to prevent header injection."""
    # Remove any characters that aren't valid in IPs
    import re
    return re.sub(r"[^0-9a-fA-F.:]", "", ip)[:45]  # Max IPv6 length
```

### 5.2 Rate Limiting Implementation

```python
from fastapi import Request, HTTPException, status
from typing import Callable
import redis.asyncio as redis

class RateLimiter:
    """Redis-based rate limiter with secure IP extraction."""

    def __init__(
        self,
        redis_url: str,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ):
        self.redis = redis.from_url(redis_url)
        self.rpm = requests_per_minute
        self.rph = requests_per_hour

    async def check(self, request: Request) -> None:
        """Check rate limit, raise 429 if exceeded."""
        ip = get_client_ip(request)

        # Check per-minute limit
        minute_key = f"ratelimit:{ip}:minute"
        minute_count = await self.redis.incr(minute_key)
        if minute_count == 1:
            await self.redis.expire(minute_key, 60)

        if minute_count > self.rpm:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {self.rpm} requests per minute"
            )

        # Check per-hour limit
        hour_key = f"ratelimit:{ip}:hour"
        hour_count = await self.redis.incr(hour_key)
        if hour_count == 1:
            await self.redis.expire(hour_key, 3600)

        if hour_count > self.rph:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {self.rph} requests per hour"
            )
```

---

## 6. Security Headers

### 6.1 Middleware Implementation

```python
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)

        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS protection (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Force HTTPS
        response.headers["Strict-Transport-Security"] = \
            "max-age=31536000; includeSubDomains"

        # Referrer policy
        response.headers["Referrer-Policy"] = \
            "strict-origin-when-cross-origin"

        # Content Security Policy
        response.headers["Content-Security-Policy"] = \
            "default-src 'self'; " \
            "script-src 'self'; " \
            "style-src 'self' 'unsafe-inline'; " \
            "img-src 'self' data: https:; " \
            "font-src 'self'; " \
            "frame-ancestors 'none'"

        # Permissions policy
        response.headers["Permissions-Policy"] = \
            "accelerometer=(), camera=(), geolocation=(), gyroscope=(), " \
            "magnetometer=(), microphone=(), payment=(), usb=()"

        return response
```

---

## 7. Incident Response Playbook

### 7.1 Immediate Actions (0-15 minutes)

```markdown
## Security Incident Response - Phase 1

### 1. DETECT
- [ ] Confirm incident is real (not false positive)
- [ ] Document initial findings in incident channel
- [ ] Assign incident commander
- [ ] Create incident ticket

### 2. ASSESS
- [ ] Determine severity level (P0/P1/P2/P3)
- [ ] Identify affected systems
- [ ] Check for data exposure
- [ ] Check for ongoing attack

### 3. NOTIFY
- [ ] Alert security team
- [ ] Notify stakeholders based on severity
- [ ] Create Slack incident channel: #incident-YYYY-MM-DD-description
```

### 7.2 Containment (15-60 minutes)

```markdown
## Security Incident Response - Phase 2

### 1. CONTAIN
- [ ] Isolate affected systems
- [ ] Block malicious IPs at WAF
- [ ] Revoke compromised credentials
- [ ] Enable enhanced logging

### 2. PRESERVE
- [ ] Capture system state (screenshots, logs)
- [ ] Backup affected databases
- [ ] Save network traffic logs
- [ ] Document timeline of events

### 3. COMMUNICATE
- [ ] Update stakeholders every 30 minutes
- [ ] Prepare customer notification if needed
- [ ] Legal notification if data breach
```

### 7.3 Secret Leak Response

```bash
#!/bin/bash
# Secret Leak Response Script

echo "=== SECRET LEAK RESPONSE ==="

# 1. IMMEDIATE - Identify leaked secret
LEAKED_KEY="sk_live_..."  # From audit/git history
echo "Leaked key pattern: ${LEAKED_KEY:0:20}..."

# 2. REVOKE - Immediately revoke the key
# Stripe example
stripe api_keys delete "$LEAKED_KEY"

# 3. AUDIT - Check for unauthorized usage
echo "Checking for unauthorized access..."
grep -r "$LEAKED_KEY" /var/log/app/ | tail -100

# 4. SCAN - Check git history
echo "Scanning git history..."
git log --all -p | grep -i "$LEAKED_KEY"

# 5. ROTATE - Generate new key
NEW_KEY=$(stripe api_keys create --type secret)
echo "New key generated (store securely)"

# 6. UPDATE - Deploy new key to production
railway variables set STRIPE_SECRET_KEY="$NEW_KEY"

echo "=== ROTATION COMPLETE ==="
```

---

## 8. Security Audit Checklist

### 8.1 Monthly Security Review

```markdown
## Monthly Security Checklist

### Code Review
- [ ] Run secret scanning (detect-secrets, gitleaks)
- [ ] Run dependency audit (npm audit, pip-audit)
- [ ] Review failed login attempts
- [ ] Check for dormant accounts

### Infrastructure
- [ ] Review IAM permissions
- [ ] Check SSL certificate expiry
- [ ] Verify backup integrity
- [ ] Test disaster recovery

### Compliance
- [ ] Review access logs for anomalies
- [ ] Update privacy policy if needed
- [ ] Verify third-party compliance
- [ ] Staff security training
```

### 8.2 Quarterly Security Audit

```markdown
## Quarterly Security Audit

### Penetration Testing
- [ ] External vulnerability scan
- [ ] Internal vulnerability scan
- [ ] Web application testing (OWASP Top 10)
- [ ] API security testing

### Credential Rotation
- [ ] Rotate database passwords
- [ ] Rotate JWT signing keys
- [ ] Rotate webhook secrets
- [ ] Rotate service account keys

### Access Review
- [ ] Remove unused service accounts
- [ ] Audit admin access
- [ ] Review API key permissions
- [ ] Disable inactive users

### Documentation
- [ ] Update security runbooks
- [ ] Review incident response procedures
- [ ] Update asset inventory
- [ ] Verify contact information
```

---

## 9. Common Vulnerabilities Reference

### 9.1 OWASP Top 10 Quick Fixes

| Vulnerability | Quick Fix |
|---------------|-----------|
| **Injection** | Use ORM/parameterized queries |
| **Broken Auth** | Implement MFA, session timeout |
| **Sensitive Data** | Encrypt at rest, use TLS |
| **XXE** | Disable DTDs in XML parsers |
| **Broken Access** | RBAC on every endpoint |
| **Security Misconfig** | Remove defaults, harden configs |
| **XSS** | Escape output, CSP headers |
| **Insecure Deserialization** | Validate all inputs, avoid pickle |
| **Known Vulnerabilities** | Regular dependency updates |
| **Insufficient Logging** | Centralized logging, alerts |

### 9.2 Code Review Security Checklist

```markdown
## PR Security Review

### Secrets
- [ ] No hardcoded credentials
- [ ] No .env files committed
- [ ] No secrets in logs

### Authentication
- [ ] Proper auth checks on endpoints
- [ ] Ownership verification
- [ ] Role-based access control

### Input/Output
- [ ] Input validation present
- [ ] SQL uses ORM
- [ ] Output escaping for HTML
- [ ] File upload validation

### Configuration
- [ ] No debug mode in production
- [ ] CORS properly configured
- [ ] Rate limiting enabled
```

---

## 10. Resources

### Internal Resources
- `docs/SECURITY_GUIDELINES.md` - Full security guidelines
- `docs/SECURITY_AUDIT_REPORT_2025-12-14.md` - Latest audit
- `docs/templates/PRIVACY_POLICY_*.md` - Privacy templates
- `docs/templates/TERMS_OF_SERVICE.md` - ToS template

### External Resources
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
- [Stripe Security Best Practices](https://stripe.com/docs/security)

---

**Last Updated:** 2026-02-26
**Next Review:** 2026-03-26
