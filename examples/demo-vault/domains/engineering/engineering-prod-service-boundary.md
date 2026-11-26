---
title: Production Service Boundary for order-gateway (生产服务修改边界)
type: note
permalink: engineering-prod-service-boundary
kind: card
domain: engineering
sensitivity: normal
status: active
ttl_tier: L1
updated_at: 2026-11-26
verified_at: 2026-11-26
aliases: [order-gateway, 生产边界, 只读排查, prod boundary]
tags: [engineering, production, boundary]
---

# Production Service Boundary (`order-gateway`)

- **Default Posture**: `order-gateway` is live in production. Default to read-only investigation; explain blast radius and obtain confirmation before modifying shared handlers or retry policies.
- **Authorization Boundary**: Code-edit approval never implies `git push`, tag creation, or production deployment.
- **Config Loading**: Runtime config loads from `<deploy_root>/conf/gateway.yaml` via `GatewayBootstrap`; sample YAML under `docs/examples/` is reference-only.

Keywords: order-gateway production read-only boundary deploy push 生产服务 只读排查 修改边界
