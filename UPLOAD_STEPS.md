# Upload / PR Steps for Spider4AI

Use these commands on your local machine (recommended if this environment blocks GitHub access):

## 1) Configure remote

```bash
git remote remove origin 2>/dev/null || true
git remote add origin https://github.com/Spider4AI/Spider4AI.git
git remote -v
```

## 2) Push branch

```bash
git push -u origin work
```

If your repository uses `main` instead:

```bash
git branch -M main
git push -u origin main
```

## 3) Open Pull Request

After push succeeds, open:

- `https://github.com/Spider4AI/Spider4AI/compare`

Choose base/target branch and submit the PR.

## Optional: push from a `.bundle` file

If you downloaded `Spider4AI-work.bundle`, restore it locally:

```bash
git clone Spider4AI-work.bundle Spider4AI
cd Spider4AI
git remote add origin https://github.com/Spider4AI/Spider4AI.git
git push -u origin work
```
