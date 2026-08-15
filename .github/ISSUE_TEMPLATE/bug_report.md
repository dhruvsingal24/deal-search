---
name: Bug report
about: Something behaves differently from the README
labels: bug
---

**What happened**

**What you expected**

**Request that triggers it**

```bash
curl -s localhost:8000/deals/search -H 'Content-Type: application/json' \
  -d '{"brand":"...","amount":0}'
```

**Response**

```json
```

**Environment**: SQLite / Postgres · local / Docker / Vercel · Python version
