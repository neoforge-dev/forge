# Service Specification: Authentication (JWT)

## Overview
The Authentication service provides a standardized way to handle JWT tokens across the FORGE portfolio. It supports HS256 and RS256 algorithms and ensures compatibility with the `forge-core` token structure.

## Interface: `JWTAuth`

### Configuration: `JWTConfig`
- `secret_key`: Secret key (HS256) or path to private key (RS256).
- `public_key`: Path to public key (required for RS256).
- `algorithm`: `HS256` (default) or `RS256`.
- `access_token_expire_minutes`: Default 30.
- `refresh_token_expire_days`: Default 7.

### Methods

#### `create_access_token`
Creates a standardized FORGE access token.
- **Parameters**: `user_id`, `email`, `domain`, `plan`, `products`, `roles`, `permissions`, `extra_claims`.
- **Returns**: Encoded JWT string.

#### `create_refresh_token`
Creates a minimal refresh token.
- **Parameters**: `user_id`, `extra_claims`.
- **Returns**: Encoded JWT string.

#### `decode_token`
Decodes and validates a JWT token.
- **Parameters**: `token`.
- **Returns**: `ForgeTokenPayload` object.
- **Throws**: `JWTError` on invalid or expired tokens.

#### `refresh_access_token`
Generates a new access token from a valid refresh token.
- **Parameters**: `refresh_token`.
- **Returns**: `(new_access_token, payload)`.

## Token Structure (JSON)
```json
{
  "sub": "uuid",
  "email": "user@example.com",
  "domain": "codeswiftr.com",
  "plan": "pro",
  "products": ["interview-simulator"],
  "roles": ["user"],
  "permissions": ["read_own"],
  "iat": 1234567890,
  "exp": 1234567890,
  "iss": "forge-core",
  "aud": "forge-services",
  "type": "access"
}
```
