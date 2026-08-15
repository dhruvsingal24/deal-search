## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem being solved, not the diff restated. -->

## Checklist

- [ ] `python manage.py test` passes
- [ ] `ruff check .` passes
- [ ] New behaviour has a test that fails without the change
- [ ] Model changes include their migration (`makemigrations --check` is in CI)
- [ ] README updated if the API shape, ranking logic or data model changed

## Ranking changes only

<!-- Delete this section if untouched. -->

- [ ] The change is in `deals/ranking.py` and stays free of Django imports
- [ ] Effective-price ordering is still the sort key
- [ ] Tie-breaks remain total, so ranking is deterministic across runs
